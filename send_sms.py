import os

# SMS imports
from twilio.rest import Client

# Web Scraping Imports
from bs4 import BeautifulSoup
import requests

# Time import to delay scrapes
import time

# Variables for Twilio info
account_sid = os.getenv("ACCOUNT_SID")
auth_token = os.getenv("AUTH_TOKEN")
to_number = os.getenv("TO_NUMBER")
from_number = os.getenv("FROM_NUMBER")

# URL (Change for class)
url = 'https://app.testudo.umd.edu/soc/search?courseId=CMSC351&sectionId=&termId=202408&_openSectionsOnly=on&creditCompare=&credits=&courseLevelFilter=ALL&instructor=&_facetoface=on&_blended=on&_online=on&courseStartCompare=&courseStartHour=&courseStartMin=&courseStartAM=&courseEndHour=&courseEndMin=&courseEndAM=&teachingCenter=ALL&_classDay1=on&_classDay2=on&_classDay3=on&_classDay4=on&_classDay5=on'

# Get page
page = requests.get(url)

# Create html soup
soup = BeautifulSoup(page.text, 'html')

# Create SMS client
client = Client(account_sid,auth_token)

# Make program run continuously
while True:
    try:

        # get content off of page
        soup = BeautifulSoup(page.text, 'html')
        currinfo = soup.find("div", {"class": "sections sixteen colgrid"})
        
        # Position of section in table NOT INDEX (1 is first element)
        pos1 = 1
        pos2 = 4

        # Get info
        sectionOne = currinfo.select("div:nth-child("+str(pos1)+")")
        sectionTwo = currinfo.select("div:nth-child("+str(pos2)+")")


        # Sleep to give time between scrapes
        time.sleep(30);

        # get new content off of page
        newSoup = BeautifulSoup(page.text, 'html')
        newInfo = newSoup.find("div", {"class": "sections sixteen colgrid"}) # seats-info-group six columns (OLD)

        # Get new info
        sectionOneNew = newInfo.select("div:nth-child("+str(pos1)+")")
        sectionTwoNew = newInfo.select("div:nth-child("+str(pos2)+")")

        if sectionOne == sectionOneNew and sectionTwo == sectionTwoNew:
            continue

        else:

            #If website has changed during sleep

            # Change cases
            if sectionTwo != sectionTwoNew:
                client.messages.create(
                to=to_number,
                from_=from_number,
                body="Section 2 opening"
                )
            else:
                #Send sms
                client.messages.create(
                to=to_number,
                from_=from_number,
                body="Section 1 opening"
                )

            # Get new info
            soup = BeautifulSoup(page.text, 'html')
            currinfo = soup.find("div", {"class": "sections sixteen colgrid"})
 
            # wait for 30 seconds until next check cycle
            time.sleep(30)
            continue

    # To handle exceptions
    except Exception as e:

        #Send sms
        client.messages.create(
        to=to_number,
        from_=from_number,
        body="It broke..."
        )
        break