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

user_id_to_name_cache = {}

def auth():
    return {"Authorization": "Bearer " + quip_access_token}

def request(url, isPost=False):
    if isPost:
        print("POST " + url)
        return requests.post(url, headers=auth())
    else:
        print("GET " + url)
        return requests.get(url, headers=auth())

def verify_access_token(say):
    global quip_access_token

    if quip_access_token == None:
        if os.environ.get("QUIP_ACCESS_TOKEN") != None:
            quip_access_token = os.environ.get("QUIP_ACCESS_TOKEN")
            if request("https://platform.quip.com/1/oauth/verify_token").status_code != 200:
                quip_access_token = None
                say("The Quip access token is invalid.")
                return False
        else:
            say("Please set your Quip access token to QUIP_ACCESS_TOKEN environment variable. You can get it from https://quip.com/dev/token.")
            return False

    return True

def get_users(user_ids):
    global user_id_to_name_cache
    request_user_ids = []
    for uid in user_ids:
        if uid in user_id_to_name_cache:
            continue
        request_user_ids.append(uid)
    data = request("https://platform.quip.com/1/users/?ids=" + ",".join(request_user_ids)).json()
    for uid in request_user_ids:
        user_id_to_name_cache[uid] = data[uid]["name"]

def search_threads(say, query):
    body = request("https://platform.quip.com/1/threads/search?only_match_titles=true&count=10&query="
            + requests.utils.quote(query)).json()

    if len(body) == 1:
        request_pdf(say, body[0])
    else:
        list_threads(say, body, "Search Results - " + query)

def recent_threads(say):
    body = request("https://platform.quip.com/1/threads/recent").json()
    arr = []
    for tid in body:
        arr.append(body[tid])
    list_threads(say, arr, "Recent Documents")

def request_pdf_by_thread_id(say, thread_id):
    thread = get_thread(thread_id)
    if thread:
        request_pdf(say, thread)
        return True
    return False

def request_pdf(say, thread):
    data = request("https://platform.quip.com/1/threads/" + thread["thread"]["id"] + "/export/pdf/async", True).json()
    if "request_id" not in data:
        say("Failed to create a PDF.")
        return

    print("Request ID:", data["request_id"])

    get_users([thread["thread"]["author_id"]])
    text = "<{}|{}> `{}` _{}_".format(
        thread["thread"]["link"],
        thread["thread"]["title"],
        thread["thread"]["id"],
        user_id_to_name_cache[thread["thread"]["author_id"]])

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
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            }
        ]
    )
    for _ in range(20):
        time.sleep(3)
        if check_pdf_status(say, thread, data["request_id"]):
            return
    say("Timed out...")

def check_pdf_status(say, thread, request_id):
    data = request("https://platform.quip.com/1/threads/" + thread["thread"]["id"] + "/export/pdf/async?request_id=" + request_id).json()
    status = data["status"]
    if status == "PROCESSING":
        return False
    elif status == "SUCCESS" or status == "PARTIAL_SUCCESS":
        text = "Download PDF"
        if status == "PARTIAL_SUCCESS":
            text += " (" + data["message"] + ")"
        say(blocks = [
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Download PDF"
                        },
                        "action_id": "download-pdf",
                        "style": "primary",
                        "url": data["pdf_url"]
                    }
                ]
            }
        ])
    elif status == "FAILURE":
        say("Failed to export PDF: " + data["message"])
    return True

def get_thread(thread_id):
    resp = request("https://platform.quip.com/1/threads/" + thread_id)
    if resp.status_code == 200:
        return resp.json()
    return None

def list_threads(say, threads, header):
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
    get_users(user_ids)

    i = 0
    for thread in threads:
        i += 1
        text = "{}. <{}|{}> `{}` _{}_".format(
            i,
            thread["thread"]["link"],
            thread["thread"]["title"],
            thread["thread"]["id"],
            user_id_to_name_cache[thread["thread"]["author_id"]])
        if thread["thread"]["type"] == "document":
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                    "accessory": {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Export to PDF"},
                        "value": thread["thread"]["id"],
                        "action_id": "export-pdf"
                    }
                })
        else:
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text}
                })

    say(blocks=blocks)

@app.command("/quiptopdf")
def command_quip_to_pdf(ack, say, command):
    ack()

    if not verify_access_token(say):
        return

    if "text" in command:
        arg = command["text"]
        if len(arg) == 11 or len(arg) == 12:
            if request_pdf_by_thread_id(say, arg):
                return
        search_threads(say, command["text"])
    else:
        recent_threads(say)

@app.action("export-pdf")
def export_button_click(body, ack, say):
    ack()

    if not verify_access_token(say):
        return

    thread_id = body["actions"][0]["value"]
    if not request_pdf_by_thread_id(say, thread_id):
        say("Thread ID {} not found.".format(thread_id))

@app.action("download-pdf")
def download_button_click(body, ack, say):
    ack()

# Start your app
if __name__ == "__main__":
    app.start(port=int(os.environ.get("PORT", 3000)))