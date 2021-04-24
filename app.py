import os
import requests
import json
import time

from slack_bolt import App

quip_access_token = None

# Initializes your app with your bot token and signing secret
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

def requestGet(url):
    print("GET " + url)
    return requests.get(url, headers={"Authorization": "Bearer " + quip_access_token})

def requestPost(url):
    print("POST " + url)
    return requests.post(url, headers={"Authorization": "Bearer " + quip_access_token})

def verifyAccessToken(say, token):
    global quip_access_token
    quip_access_token = token
    if requestGet("https://platform.quip.com/1/oauth/verify_token").status_code != 200:
        quip_access_token = None
        say("The access token is invalid.")
        return False
    say("Verified the access token successfully.")
    return True

def getUsers(user_ids):
    return requestGet("https://platform.quip.com/1/users/?ids=" + ",".join(user_ids)).json()

def searchDocuments(say, query):
    body = requestGet("https://platform.quip.com/1/threads/search?only_match_titles=true&count=10&query="
            + requests.utils.quote(query)).json()

    if len(body) == 1:
        createPdfRequest(say, body[0])
    else:
        listThreads(say, body, "Search Results - " + query)

def recentDocuments(say):
    body = requestGet("https://platform.quip.com/1/threads/recent").json()
    arr = []
    for tid in body:
        arr.append(body[tid])
    listThreads(say, arr, "Recent Documents")

def createPdfRequest(say, thread):
    data = requestPost("https://platform.quip.com/1/threads/" + thread["thread"]["id"] + "/export/pdf/async").json()
    say(
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Exporting PDF"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Title: <{}|{}>\nThread ID: {}\nRequest ID: {}".format(
                    thread["thread"]["link"],
                    thread["thread"]["title"],
                    thread["thread"]["id"],
                    data["request_id"])}
            }
        ]
    )
    for _ in range(20):
        time.sleep(3)
        if checkPdfStatus(say, thread, data["request_id"]):
            return
    say("Timed out...")

def checkPdfStatus(say, thread, request_id):
    data = requestGet("https://platform.quip.com/1/threads/" + thread["thread"]["id"] + "/export/pdf/async?request_id=" + request_id).json()
    status = data["status"]
    if status == "PROCESSING":
        return False
    elif status == "SUCCESS":
        say("Generated PDF: " + data["pdf_url"])
    elif status == "PARTIAL_SUCCESS":
        say("Generated PDF partially: " + data["pdf_url"] + " (" + data["message"] + ")")
    elif status == "FAILURE":
        say("Failed to export PDF " + data["message"])
    return True

def getThread(thread_id):
    resp = requestGet("https://platform.quip.com/1/threads/" + thread_id)
    if resp.status_code == 200:
        return resp.json()
    return None

def listThreads(say, threads, header):
    blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header
                }
            },
            {
                "type": "divider"
            }]

    if len(threads) == 0:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "Not found."},
            })
        say(blocks=blocks)
        return

    user_ids = []
    for thread in threads:
        user_ids.append(thread["thread"]["author_id"])
    users = getUsers(user_ids)

    i = 0
    for thread in threads:
        if thread["thread"]["type"] != "document":
            continue
        i += 1
        text = "{}. [{}] <{}|{}> (Author: {})\n".format(
            i,
            thread["thread"]["id"],
            thread["thread"]["link"],
            thread["thread"]["title"],
            users[thread["thread"]["author_id"]]["name"])
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "PDF"},
                    "action_id": "button_click"
                }
            })

    say(blocks=blocks)

@app.command("/quiptopdf")
def command_quip_to_pdf(ack, say, command):
    ack()

    if quip_access_token == None:
        if "text" in command:
            verifyAccessToken(say, command["text"])
        else:
            say("Please specify your Quip access token. You can get it from https://quip.com/dev/token.")
        return

    if "text" in command:
        arg = command["text"]
        if len(arg) == 11 or len(arg) == 12:
            thread = getThread(arg)
            if thread:
                createPdfRequest(say, thread)
                return
        searchDocuments(say, command["text"])
    else:
        recentDocuments(say)

# Start your app
if __name__ == "__main__":
    app.start(port=int(os.environ.get("PORT", 3000)))