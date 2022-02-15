import sys, os,io
import cwConfig
import doorKey
import lib
from datetime import date,tzinfo,datetime, timedelta
from ConnectPyse.service import ticket_notes_api, ticket_note,ticket,tickets_api
from ConnectPyse.system import member,members_api,document_api
from ConnectPyse.schedule import schedule_entries_api,schedule_entry
import requests
import tabula
import getpass
from alive_progress import alive_bar,alive_it
import pandas as pd
import pyodbc
import time 


### Configs
config = doorKey.tangerine()
cwAUTH=config['cwAUTH']
cwDocumentHeaders = config['cwDocumentHeaders']

### fBAR folder location
prefix = r"C:\Users"
localuser = getpass.getuser()
suffix= r"\Southeastern Computer Associates, LLC\GCA Deployment - Documents\Database\Daily Data Sets\fBAR"
pdFolder = prefix + "\\"+ localuser + suffix

### Set up for start = beginning of week
today = date.today()
start = today - timedelta(days=today.weekday())
end = start + timedelta(days=6)

# start = '2022-01-10'
start = "["+str(start)+"]"


### Create an easy dictionay class
class my_dictionary(dict): 
    # __init__ function 
    def __init__(self): 
        self = dict()   
    # Function to add key:value 
    def add(self, key, value): 
        self[key] = value 

def cleanUp():
    for f in os.listdir(pdFolder):
        print(f)
        os.remove(os.path.join(pdFolder, f))

### Cleans up any left overs in fBar folder
cleanUp()

### Generate MFA Request
try:
    lib.refreshToken()
    time.sleep(5)
except Exception as e:
    lib.getToken()
    time.sleep(10)

### Start should equal beginning of current week
print(start)

### Get fBAR Tickets
gt = tickets_api.TicketsAPI(url=cwConfig.cwURL, auth=cwAUTH)
gt.conditions = 'company/identifier="Georgia Cyber Academy" AND summary contains "Backup Status Report"  AND _info/dateEntered > {start}'.format(start=start)
print(gt.conditions)
gt.pageSize = 10
gt.orderBy = '_info/dateEntered'
gt.fields = 'id'
gt = gt.get_tickets()
ls = list(gt)
id_list = []
for i in ls: 
  x = i.id
  y = i.status['name']
  z = i._info['dateEntered']
  # print(x)
  id_list.append(x)     

### Get Ticket Info
doc_list=[]
id_dict = my_dictionary() 
for i in id_list:
  o = tickets_api.TicketsAPI(url=cwConfig.cwURL, auth=cwAUTH)
  d = document_api.DocumentAPI(url=cwConfig.cwURL, auth=cwAUTH)
  # Retrieves ticket by Ticket ID
  a_ticket = o.get_ticket_by_id(i)
  # print(a_ticket)
  # Retrieves Document from ticket
  myDocs = d.get_documents(a_ticket)
  # Retrieves title
  for doc in myDocs:
    if doc.title.endswith('.pdf'):
      print("\n",doc.id,doc.title,doc._info['lastUpdated'])
      # print(doc)
      # docTITLE=doc.title
      # docTITLE=docTITLE.replace(':',"")
      docID=doc.id
      doc_list.append(docID)
      id_dict.add(i,docID)

### Get Download and Convert to DataFrame
appended_data=[]
ticket_dict=my_dictionary()
test_list=[]
for tickID,docID in alive_it(id_dict.items()):
  url = cwConfig.cwURL+'system/documents/{document_id}/download'.format(document_id=docID)
  response = requests.get(url=url, headers=cwDocumentHeaders)
  with io.BytesIO(response.content) as fh:
      tl = tabula.read_pdf(input_path=fh, pages = "all",output_format="dataframe", stream=True)
      
      ### Convert the tabula list to DataFrame
      fBarDF = tl[0]
      # print(type(fBarDF))
      
      ### Create List(containing ticket numbers) to add to the DataFrame
      list_count = fBarDF['Backup Name'].tolist()
      x = len(list_count)
      y = str(tickID)
      z_list = [y]*x
      fBarDF['TicketNumber']=z_list
      
      ### Rename Column Names
      fBarDF = fBarDF.rename(columns={'Backup Name': 'Device Name'})
      
      ### Isolate Problem Column
      fBarDF = fBarDF.loc[fBarDF['Status'].str.contains('Problem')]
      
      ### Append the database
      appended_data.append(fBarDF)
      ticket_dict.add(tickID,docID)

### Concat the DataFrames into 1 DataFrame
appended_data = pd.concat(appended_data)
appended_data = appended_data.reset_index(drop=True)
col_one_list = appended_data['Device Name'].tolist()
colLen = len(col_one_list)
comp_dict=my_dictionary()
comp_dict_empty = my_dictionary()


### Checks automate to see if the computer is found
for i in alive_it(col_one_list):
  data,empty_data = lib.getSpecificComputer(i,1)
  comp_dict.update(data)
  comp_dict_empty.update(empty_data)

#print(comp_dict)  
#print(appended_data)

### Adds serial numbers to DataFrame based on automate status
appended_data['Serial Number'] = appended_data['Device Name'].map(comp_dict)
#print(appended_data)
appended_data['Automate Status'] = appended_data['Device Name'].map(comp_dict_empty)
print(appended_data)

### Upload DataFrame to GCA Database
data = appended_data

conn = pyodbc.connect(
    'Driver={ODBC Driver 17 for SQL Server};'
    'Server='+(config['database']['Server'])+';'
    'Database=IsolatedSafety;'
    'UID='+(config['database']['UID'])+';'
    'PWD='+(config['database']['PWD'])+';'
)
try:
    cursor = conn.cursor()
except pyodbc.ProgrammingError as error:
    print(error)
    input("Not able to connect to Database.")


### Map columns of DataFrame with SQL Database
column_mapping = {'Device Name': '"Backup Name"',
                'Status': 'Status',
                'Serial Number': '"Serial Number"',
                'Automate Status': '"Automate Status"',
                'TicketNumber':'TicketNumber'}

pdfDF = pd.DataFrame(data, columns=list(column_mapping.keys())).astype(str).where(pd.notnull(data), None).replace('\.0', '', regex=True)
print(pdfDF)


#### COMMON VARIABLES
table_name = 'dbo.TEMP_Backup_Report'  # name of primary table to work with in database
select_fields = ", ".join(column_mapping.values())
values_list = ", ".join('?' * len(column_mapping.values()))
insert_query = f"INSERT INTO {table_name} ({select_fields}) VALUES ({values_list})"
delete_query = f"DELETE FROM {table_name};"
process_query = f"EXEC IsolatedSafety.dbo.uspGenBackupIssueReport;"

### empty specified table prior to import
try:
    cursor.execute(delete_query)
except:
    print("Unable to delete query")
    pass

with alive_bar(len(pdfDF.index)) as bar:## Alive bar for progress 
        try:
            for index, row in pdfDF.iterrows():
                cursor.execute(insert_query, *row)
                bar()
        except pyodbc.ProgrammingError as error:
            print(error)
try:
    cursor.execute(process_query)
except Exception as e:
    print(e)
    print("process_query causing issues.")

conn.commit()

print("SQL Updated")

time.sleep(3)

### Retrieve Backup report
GenBackupReportQuery=f"EXEC IsolatedSafety.dbo.uspGenBackupIssueReport;"
BackupReport = pd.read_sql(GenBackupReportQuery , conn)
print(BackupReport)
conn.close()

### Retrieve ticket IDs based on information retrieved from DataBase
UniqueNames = BackupReport.TicketNumber.unique() 
print(UniqueNames)
# input("Press enter to continue")
num=0
for tID in UniqueNames:
    idName=tID
    idName=str(idName)
    num+=1
    numS=str(num)
    
    ### Creation of Excel File
    df= BackupReport.loc[BackupReport['TicketNumber'].astype(str).str.contains(idName)]
    df = df.reset_index(drop=True)
    print(df)
    file_name = '{}.xlsx'.format(idName+' Backup Report')
    
    fbar_file=pdFolder+'\\'+file_name
    # fbar_file=r'C:\Users\Mbrown\Southeastern Computer Associates, LLC\GCA Deployment - Documents\Database\Daily Data Sets\fBAR\{}'.format(file_name)
    df.to_excel(fbar_file)
    

    ### Get File info
    o = tickets_api.TicketsAPI(url=cwConfig.cwURL, auth=cwAUTH)
    d = document_api.DocumentAPI(url=cwConfig.cwURL, auth=cwAUTH)
    
    a_ticket = o.get_ticket_by_id(ticket_id=tID)
    myDocs = d.get_documents(a_ticket)
    for doc in myDocs:
        if doc.title.endswith('.pdf'):
            print("\n")
            print("Ticket Number: ",tID)
            print(doc.title)
    print("\n")  
    
    ### Upload Document to Ticket
    f = os.path.join(os.path.curdir, fbar_file)
    a_document = d.create_document(a_ticket, 'Backup Report', file_name, open(fbar_file, 'rb'))
    print(a_document.title)
    
    ### Remove current Assigned scheduleEntries
    try:
        gse = schedule_entries_api.ScheduleEntriesAPI(url=cwConfig.cwURL, auth=cwAUTH)
        gse.conditions = 'objectId ={} AND doneFlag=False'.format(tID)
        gse.pageSize = 10
        gse = gse.get_schedule_entries()
        ls = list(gse)
        for i in ls:
            print(i.id)
            print(i.member['identifier'])
            if i.member['identifier'] == 'MBrown' or i.member['identifier'] == 'MShull' or i.member['identifier'] == 'NTraverse' or i.member['identifier'] == 'BKrusac' or i.member['identifier'] == None or i.member['identifier'] == 'JBowman' or i.member['identifier'] == 'JTrimble' or i.member['identifier'] == 'NBowman' or i.member['identifier'] == '':
                assign_ticket = schedule_entries_api.ScheduleEntriesAPI(url=cwConfig.cwURL, auth=cwAUTH)
                assign_ticket.update_schedule_entry(i.id,"ownerFlag",False) 
                assign_ticket.update_schedule_entry(i.id,"doneFlag",True) 
                 ### Assigns ticket
                itID = int(tID)
                assign_ticket = schedule_entries_api.ScheduleEntriesAPI(url=cwConfig.cwURL, auth=cwAUTH)
                assigned = schedule_entry.ScheduleEntry({"objectId": itID, "member":{"identifier":"BKrusac"},"type": { "identifier": "S" }})
                assign_ticket.create_schedule_entry(assigned)
                assigned = schedule_entry.ScheduleEntry({"objectId": itID, "member":{"identifier":"MShull"},"type": { "identifier": "S" },"ownerFlag": True})
                assign_ticket.create_schedule_entry(assigned)
                assigned = schedule_entry.ScheduleEntry({"objectId": itID, "member":{"identifier":"NTraverse"},"type": { "identifier": "S" }})
                assign_ticket.create_schedule_entry(assigned)
                   ### Creates a ticket note
                ticket_notes = ticket_notes_api.TicketNotesAPI(url=cwConfig.cwURL, auth=cwAUTH, ticket_id=tID)
                note = ticket_note.TicketNote({"text":"Assigned / BKrusac, MShull, NTraverse /\nThis ticket has been updated with a new Excel Spreadsheet for the FBAR Backup computers with issues.\nPlease see {} and assign to an appropriate engineer with FBAR training.".format(file_name), "detailDescriptionFlag": True, "internalAnalysisFlag": True ,"externalFlag": False})
                ticket_notes.create_ticket_note(note)
            else:
                ticket_notes = ticket_notes_api.TicketNotesAPI(url=cwConfig.cwURL, auth=cwAUTH, ticket_id=tID)
                note = ticket_note.TicketNote({"text":"This ticket has been updated with a new Excel Spreadsheet for the FBAR Backup computers with issues.\nPlease see {}.".format(file_name), "detailDescriptionFlag": True, "internalAnalysisFlag": True ,"externalFlag": False})
                ticket_notes.create_ticket_note(note)
    except Exception as e:
        print(e)
        print('Broke at "Remove current Assigned acheduleEntries"')


   
    

 

 