import os

# SMS imports
from twilio.rest import Client

# Web Scraping Imports
from bs4 import BeautifulSoup
import requests

# Time import to delay scrapes
import time

# Variables for Twilio info
account_sid = "AC6e173fbe537c10c06893367f334dd284"
auth_token = "98e0e487f0b8eb6a9576cb9ab6b57ad2"

# URL (Change for class)
url = 'https://app.testudo.umd.edu/soc/search?courseId=CMSC351&sectionId=&termId=202408&_openSectionsOnly=on&creditCompare=&credits=&courseLevelFilter=ALL&instructor=&_facetoface=on&_blended=on&_online=on&courseStartCompare=&courseStartHour=&courseStartMin=&courseStartAM=&courseEndHour=&courseEndMin=&courseEndAM=&teachingCenter=ALL&_classDay1=on&_classDay2=on&_classDay3=on&_classDay4=on&_classDay5=on'

# Get page
page = requests.get(url)

# Create html soup
soup = BeautifulSoup(page.text, 'html')

# Create SMS client
client = Client(account_sid,auth_token)

# Wait (not necessary)
time.sleep(10);

# Make program run continuously
while True:
    try:

        # get info off of page
        soup = BeautifulSoup(page.text, 'html')
        currinfo = soup.find("div", {"class": "sections sixteen colgrid"})
        
        # Position of teacher in table NOT INDEX (1 is first element)
        pos1 = 1
        pos2 = 4

        # Get info
        justin1 = currinfo.select("div:nth-child("+str(pos1)+")")
        herve1 = currinfo.select("div:nth-child("+str(pos2)+")")
        print (justin1)
        print (herve1)


        # Sleep to give time between scrapes
        time.sleep(30);

        # get new info off of page
        newSoup = BeautifulSoup(page.text, 'html')
        newInfo = newSoup.find("div", {"class": "sections sixteen colgrid"}) # seats-info-group six columns (OLD)

        # Get new info
        justin2 = newInfo.select("div:nth-child("+str(pos1)+")")
        herve2 = newInfo.select("div:nth-child("+str(pos2)+")")
        print (justin2)
        print (herve2)

        if justin1 == justin2 and herve1 == herve2:
            continue

        else:

            #If website has changed during sleep
            print('change')

            # Opening cases (Herve vs Justin)
            if herve1 != herve2:
                #Send sms (WORKING)
                client.messages.create(
                to="+12073528818",
                from_="+18333551367",
                body="Herve opening"
                )
                print('CHANGES TO HERVE')
            else:
                #Send sms (WORKING)
                client.messages.create(
                to="+12073528818",
                from_="+18333551367",
                body="Justin opening"
                )
                print('CHANGES TO JUSTIN')

            # Get new info
            soup = BeautifulSoup(page.text, 'html')
            currinfo = soup.find("div", {"class": "sections sixteen colgrid"})
 
            # wait for 30 seconds
            time.sleep(30)
            continue

    # To handle exceptions
    except Exception as e:
        print("error")

        #Send sms (WORKING)
        client.messages.create(
        to="+12073528818",
        from_="+18333551367",
        body="It broke..."
        )
        break


