import os
import requests
import json
import time
from urllib.parse import unquote, quote
from datetime import datetime

from slack_bolt import App
from slack_sdk.errors import SlackApiError

QUIP_END_POINT = "https://platform.quip.com/1/"
QUIP_ACCESS_TOKEN = None

# Initializes your app with your bot token and signing secret
app = App(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET")
)

user_id_to_name_cache = {}


def auth():
    return {"Authorization": "Bearer " + QUIP_ACCESS_TOKEN}


def request(url, isPost=False):
    if url.find("https://") == -1:
        url = QUIP_END_POINT + url

    if isPost:
        print("POST " + url)
        return requests.post(url, headers=auth())
    else:
        print("GET " + url)
        return requests.get(url, headers=auth())


def verify_access_token(say):
    global QUIP_ACCESS_TOKEN

    if QUIP_ACCESS_TOKEN == None:
        if os.environ.get("QUIP_ACCESS_TOKEN") != None:
            QUIP_ACCESS_TOKEN = os.environ.get("QUIP_ACCESS_TOKEN")
            if request("oauth/verify_token").status_code != 200:
                QUIP_ACCESS_TOKEN = None
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
        if uid not in user_id_to_name_cache:
            request_user_ids.append(uid)
    if len(request_user_ids) == 0:
        return
    data = request("users/?ids=" + ",".join(request_user_ids)).json()
    for uid in request_user_ids:
        user_id_to_name_cache[uid] = data[uid]["name"]


def search_threads(query):
    return request("threads/search?only_match_titles=true&count=10&query=" + quote(query)).json()


def recent_threads():
    body = request("threads/recent").json()
    return list(map(lambda tid: body[tid], body))


def request_pdf(say, client, channel_id, thread):
    data = request("threads/" + thread["thread"]["id"] + "/export/pdf/async", True).json()
    if "request_id" not in data:
        say("Failed to create a PDF.")
        return

    print("Request ID:", data["request_id"])

    blocks = [
        {
            "type": "header",
            "text": {
                    "type": "plain_text",
                    "text": "Exporting to PDF"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": get_document_info(thread)
            },
        }
    ]
    say(blocks=blocks)

    for _ in range(60):
        time.sleep(3)
        if check_pdf_status(say, client, channel_id, thread, data["request_id"]):
            return

    say("Timed out...")


def check_pdf_status(say, client, channel_id, thread, request_id):
    data = request("threads/" + thread["thread"]["id"] + "/export/pdf/async?request_id=" + request_id).json()
    status = data["status"]
    if status == "PROCESSING":
        return False
    elif status == "SUCCESS" or status == "PARTIAL_SUCCESS":
        text = "Download PDF"
        if status == "PARTIAL_SUCCESS":
            text += " (" + data["message"] + ")"

        pdf_url = data["pdf_url"]
        print("PDF URL original:", pdf_url)
        file_name = quote(pdf_url[pdf_url.rindex("name=") + 5:])
        pdf_url = pdf_url[:pdf_url.rindex("name=") + 5] + file_name
        print("PDF URL encoded:", pdf_url)

        attach_pdf(say, client, channel_id, pdf_url, request_id)
    elif status == "FAILURE":
        say("Failed to export PDF: " + data["message"])
    return True


def attach_pdf(say, client, channel_id, pdf_url, request_id):
    file_name = pdf_url[pdf_url.rindex("name=") + 5:]

    if not os.path.exists("/tmp"):
        os.makedirs("/tmp")

    file_path = "/tmp/" + request_id + ".pdf"
    pdf_data = request(pdf_url).content
    with open(file_path, "wb") as file:
        file.write(pdf_data)
        print("File saved: {}".format(file_path))

    try:
        result = client.files_upload(
            channels=channel_id,
            title=unquote(file_name),
            file=file_path,
            filetype="pdf"
        )
    except SlackApiError as e:
        say("Error uploading PDF: {}".format(e))
    finally:
        os.remove(file_path)
        print("File deleted: {}".format(file_path))


def get_thread(thread_id):
    resp = request("threads/" + thread_id)
    if resp.status_code == 200:
        return resp.json()
    return None


def get_document_info(thread):
    get_users([thread["thread"]["author_id"]])
    return "<{}|{}> `{}` _{} {}_".format(
        thread["thread"]["link"],
        thread["thread"]["title"],
        thread["thread"]["id"],
        user_id_to_name_cache[thread["thread"]["author_id"]],
        formatDate(thread["thread"]["updated_usec"]))


def formatDate(ts):
    return datetime.fromtimestamp(ts//1000000).strftime("%Y-%m-%d %H:%M:%S")


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
        }
    ]

    if len(threads) == 0:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Not found."
                },
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
        text = "{}. ".format(i) + get_document_info(thread)
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": text
                },
                "accessory": {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Export"
                    },
                    "value": thread["thread"]["id"],
                    "action_id": "export-pdf"
                }
            }
        )

    say(blocks=blocks)


@app.command("/quiptopdf")
def command_quip_to_pdf(ack, say, client, command):
    ack()

    if not verify_access_token(say):
        return

    channel_id = command["channel_id"]
    print("channel ID:", channel_id)

    if "text" in command:
        query = command["text"]
        if len(query) == 11 or len(query) == 12:
            thread = get_thread(query)
            if thread:
                request_pdf(say, client, channel_id, thread)
                return

        results = search_threads(command["text"])
        if len(results) == 1:
            request_pdf(say, client, channel_id, results[0])
        else:
            list_threads(say, results, "Search Results - " + query)

    else:
        results = recent_threads()
        list_threads(say, results, "Recent Documents")


@app.action("export-pdf")
def export_button_click(ack, say, client, body):
    print("=== export_button_click ===")
    ack()

    if not verify_access_token(say):
        return

    thread_id = body["actions"][0]["value"]
    channel_id = body["channel"]["id"]

    print("Thread ID:", thread_id)
    print("channel ID:", channel_id)

    thread = get_thread(thread_id)
    if thread:
        request_pdf(say, client, channel_id, thread)
    else:
        say("Thread ID {} is not found.".format(thread_id))


# Start your app
if __name__ == "__main__":
    app.start(port=int(os.environ.get("PORT", 3000)))
