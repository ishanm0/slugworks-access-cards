import datetime
import json
import logging
import os.path
from threading import Thread
from typing import Any, Callable, Iterable, Mapping

import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Change directory to repository root
path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.chdir(path)

# Create a new directory for logs if it doesn't exist
if not os.path.exists(path + "/logs/sheet"):
    os.makedirs(path + "/logs/sheet")

# create new logger with all levels
logger = logging.getLogger("sheet")
logger.setLevel(logging.DEBUG)

# get reader id (0 is the control pi, any other number is a reader pi zero)
try:
    reader_file = json.load(open("common/ID.json"))
except FileNotFoundError:
    logger.error("No ID.json file found.")
    exit(1)
reader_id = reader_file["id"]

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# The ID and sheet names of the access cards sheet.
SPREADSHEET_ID = "1X7VJ9jRQGx0ZryXbvbmff09eOawymLg-DvTY7FYxN2E"
STUDENTS_SHEET = "Students"
STAFF_SHEET = "Staff"
MODULES_SHEET = "Modules"  # contains a mapping from room accesses to combinations of modules ("AND(5, 6, OR(7, 8))" etc)
ACCESSES_SHEET = (
    "Accesses"  # contains a mapping from room accesses to reader ID numbers
)
READERS_SHEET = "Readers"  # contains statuses of the readers
LOG_SHEET = "Log"  # contains a log of all card reads
CANVAS_STATUS_SHEET = "Canvas Status"  # contains the last update time of the Canvas data & the current status of the update

# max lines to send to Google Sheets at a time
SEND_BLOCK = 500

ENABLE_SCAN_LOGS = False  # disable scan logs - considered P3 data due to tracking locations of students (can add back in for testing or when a secure logging system is implemented)

# Dataframes/dictionaries to store the sheet data
student_data = None
staff_data = None
module_data = None
access_data = None
reader_data = None

# the data is limited to UIDs and accesses only
limited_data = False

# status names
statuses = ["No Access", "Access"]

# headers for this_reader dictionary
reader_headers = [
    "id",
    "status",
    "location",
    "alarm",
    "alarm_delay_min",
    "alarm_status",
    "needs_update",
    "last_checked_in",
]

# status/alarm/etc info for this reader
this_reader = None

# headers for access_data dataframe
access_headers = None

# list of room names
rooms = list()

# last update time of the sheet data
last_update_time = None

# last check-in time of the reader
last_checkin_time = None

# last update time of the Canvas data
last_canvas_update_time = None

# whether Canvas is currently updating/needs updating
canvas_is_updating = None
canvas_needs_update = None

# how many lines were read from the student and staff sheets - used to determine how many blank lines to add when writing
student_sheet_read_len = 0
staff_sheet_read_len = 0

creds = None
# The file token.json stores the user's access and refresh tokens, and is
# created automatically when the authorization flow completes for the first
# time.
if os.path.exists("common/token.json"):
    creds = Credentials.from_authorized_user_file("common/token.json", SCOPES)
elif not os.path.exists("common/credentials.json"):
    logger.error("No credentials.json file found.")
    exit(1)
# If there are no (valid) credentials available, let the user log in (assuming credentials.json exists).
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            "common/credentials.json", SCOPES
        )
        creds = flow.run_local_server(port=44649)
    # Save the credentials for the next run
    with open("common/token.json", "w") as token:
        token.write(creds.to_json())

try:
    # Call the Sheets API
    service = build("sheets", "v4", credentials=creds)
    g_sheets = service.spreadsheets()
except HttpError as e:
    logger.error(e)
    exit(1)


def get_sheet_data(limited=None):
    """
    Get the student and staff data from the Google Sheets document.

    limited: bool: if True, only the UID and accesses will be retrieved, else all data will be retrieved (Name, CruzID, Canvas ID).

    Returns True if the data was retrieved, or False if it was not.
    """
    global student_data, staff_data, module_data, access_data, rooms, limited_data, last_update_time, student_sheet_read_len, staff_sheet_read_len

    # set limited data mode
    if limited is not None:
        limited_data = limited
    try:
        # set the last update time to now
        last_update_time = datetime.datetime.now()
        # get the students sheet - skip the first 4 columns if in limited data mode
        students = (
            g_sheets.values()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                range=STUDENTS_SHEET + ("!E1:ZZ" if limited_data else ""),
            )
            .execute()
        )
        values = students.get("values", [])

        if not values:
            logger.error("No data found.")
            exit()

        # fill in empty cells - this is necessary for the dataframes to be created
        values = [r + [""] * (len(values[0]) - len(r)) for r in values]

        # set the length of the sheet read
        student_sheet_read_len = len(values)

        # create the student data dataframe - first row is the headers
        student_data = pd.DataFrame(
            values[1:] if len(values) > 1 else None,
            columns=values[0],
        )

        # get the rooms from the headers
        rooms = student_data.columns.tolist()[(1 if limited_data else 5) :]

        # get the staff sheet, get only the first column if in limited data mode
        staff = (
            g_sheets.values()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                range=STAFF_SHEET + ("!A1:A" if limited_data else ""),
            )
            .execute()
        )
        values = staff.get("values", [])

        # fill in empty cells - this is necessary for the dataframes to be created
        values = [r + [""] * (len(values[0]) - len(r)) for r in values]
        # set the length of the sheet read
        staff_sheet_read_len = len(values)

        # create the staff data dataframe - first row is the headers
        staff_data = pd.DataFrame(
            values[1:] if len(values) > 1 else None,
            columns=values[0],
        )

        # get the modules sheet
        modules = (
            g_sheets.values()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                range=MODULES_SHEET,
            )
            .execute()
        )
        values = modules.get("values", [])

        # fill in empty cells
        values = [r + [""] * (len(values[0]) - len(r)) for r in values]

        # create the modules data dataframe - first row is the headers
        module_data = pd.DataFrame(
            values[1:] if len(values) > 1 else None,
            columns=values[0],
        )

        # if this is not the control pi, get the reader data
        if reader_id > 0:
            # create the access headers - column names for the access data
            access_headers = ["id", "staff"] + rooms + ["no_access"]

            # get the accesses sheet - get only the row for this reader, and number of columns is based on the length of the access headers
            # start at 'A' and go to the character corresponding to the length of the access headers
            accesses = (
                g_sheets.values()
                .get(
                    spreadsheetId=SPREADSHEET_ID,
                    range=ACCESSES_SHEET
                    + f"!A{reader_id+1}:{str(chr(ord('A') + len(access_headers)))}{reader_id+1}",
                )
                .execute()
            )
            values = accesses.get("values", [])

            # initialize the access data dictionary
            access_data = dict()
            # fill in the dict with the values from the sheet row
            for i, r in enumerate(values[0]):
                if access_headers[i] == "id":
                    # if the column is the id, convert it to an int (0 if empty)
                    access_data["id"] = int(0 if not r else r)
                elif not r:
                    # if the value is empty, set it to None
                    access_data[access_headers[i]] = None
                else:
                    # if the value is not empty, split it by the comma and space - color code and alarm timeout
                    r = r.split(", ")
                    # if the alarm timeout is not empty, convert it to an int
                    if r[1]:
                        r[1] = int(r[1])
                    # set the value in the access data dict as a tuple
                    access_data[access_headers[i]] = tuple(r)

        # call get_reader_data() and return its result (True if successful, False if not) - presumably get_sheet_data has succeeded
        return get_reader_data()
    except HttpError as e:
        logger.error(e)
        return False


def get_reader_data():
    """
    Get the reader data from the Google Sheets document.

    Returns True if the data was retrieved, or False if it was not.
    """
    global reader_data, this_reader
    try:
        # get the readers sheet
        readers = (
            g_sheets.values()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                range=READERS_SHEET,
            )
            .execute()
        )
        values = readers.get("values", [])
        values = [r + [""] * (len(values[0]) - len(r)) for r in values]
        reader_data = pd.DataFrame(
            values[1:] if len(values) > 1 else None,
            columns=reader_headers,
        )

        this_reader = dict()
        for i, r in enumerate(values[reader_id + 1]):
            this_reader[reader_headers[i]] = r

        this_reader["id"] = 0 if not len(this_reader["id"]) else int(this_reader["id"])
        this_reader["alarm"] = (
            False if not len(this_reader["alarm"]) else this_reader["alarm"] == "ENABLE"
        )
        this_reader["alarm_delay_min"] = (
            0
            if not len(this_reader["alarm_delay_min"])
            else int(this_reader["alarm_delay_min"])
        )
        this_reader["needs_update"] = this_reader["needs_update"] == "PENDING"

        get_canvas_status_sheet()

        return True
    except HttpError as e:
        logger.error(e)
        return False


def check_in(alarm_status=False):
    global last_checkin_time, this_reader
    """
    Update the reader's last checked in time and needs update status.

    alarm_status: bool: the alarm status (True if the alarm was triggered, False if it was not, and None if it's tagged out).

    Returns True if the data was updated, or False if it was not.
    """
    if not get_reader_data():
        return False
    if this_reader["alarm"] == "DISABLE":
        this_reader["alarm_status"] = "DISABLED"
    else:
        if reader_id == 0:
            this_reader["alarm_status"] = ""
        elif alarm_status is None:
            this_reader["alarm_status"] = "TAGGED OUT"
        elif alarm_status:
            this_reader["alarm_status"] = "ALARM"
        else:
            this_reader["alarm_status"] = "OK"

    last_checkin_time = datetime.datetime.now()
    this_reader["last_checked_in"] = str(last_checkin_time)
    if this_reader["needs_update"]:
        logger.info("Update needed...")
        get_sheet_data()
    this_reader["needs_update"] = "DONE"

    try:
        _ = (
            g_sheets.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=READERS_SHEET + f"!F{reader_id+2}:H{reader_id+2}",
                valueInputOption="USER_ENTERED",
                body={"values": [list(this_reader.values())[-3:]]},
            )
            .execute()
        )
    except HttpError as e:
        logger.error(e)
        return False
    return True


def get_canvas_status_sheet():
    global last_canvas_update_time, canvas_is_updating, canvas_needs_update
    """
    Get the time of the last Canvas update.

    Returns the time of the last Canvas update.
    """

    try:
        values = (
            g_sheets.values()
            .get(
                spreadsheetId=SPREADSHEET_ID,
                range=CANVAS_STATUS_SHEET + "!A2:B2",
            )
            .execute()
        ).get("values", [])

        canvas_is_updating = True if values[0][0] == "UPDATING" else False
        canvas_needs_update = True if values[0][0] == "PENDING" else False
        last_canvas_update_time = datetime.datetime.strptime(
            values[0][1], "%Y-%m-%d %H:%M:%S"
        )
        return True
    except HttpError as e:
        logger.error(e)
        return None


def set_canvas_status_sheet(updating_now, update_time=None):
    """
    Set the time of the last Canvas update.

    updating_now: bool: True if Canvas is currently updating, False if it is not.

    Returns True if the data was set, or False if it was not.
    """
    global last_canvas_update_time
    try:
        _ = (
            g_sheets.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=CANVAS_STATUS_SHEET + "!A2:B2",
                valueInputOption="USER_ENTERED",
                body={
                    "values": [
                        [
                            "UPDATING" if updating_now else "DONE",
                            (
                                str(update_time)
                                if not updating_now and update_time
                                else str(last_canvas_update_time)
                            ),
                        ]
                    ]
                },
            )
            .execute()
        )
        last_canvas_update_time = update_time
        return True
    except HttpError as e:
        logger.error(e)
        return False


def update_canvas():
    """
    Set the Canvas update status to pending.

    Returns True if the data was set, or False if it was not.
    """
    global canvas_is_updating
    get_canvas_status_sheet()
    if canvas_is_updating:
        return False
    try:
        _ = (
            g_sheets.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=CANVAS_STATUS_SHEET + "!A2:A2",
                valueInputOption="USER_ENTERED",
                body={"values": [["PENDING"]]},
            )
            .execute()
        )
        return True
    except HttpError as e:
        logger.error(e)
        return False


def student_exists(cruzid=None, canvas_id=None, uid=None):
    """
    Check if a student exists in the database.

    cruzid: str: the student's CruzID if not in limited data mode.
    canvas_id: str: the student's Canvas ID if not in limited data mode.
    uid: str: the student's card UID.

    Returns True if the student exists, or False if they do not.
    """
    return bool(
        (not limited_data and cruzid and cruzid in student_data["CruzID"].values)
        or (
            not limited_data
            and canvas_id
            and canvas_id in student_data["Canvas ID"].values
        )
        or (uid and uid in student_data["Card UID"].values)
    )


def new_student(first_name, last_name, cruzid, canvas_id=None, uid=None, accesses=None):
    """
    Add a new student to the database (must not be in limited data mode).

    first_name: str: the student's first name.
    last_name: str: the student's last name.
    cruzid: str: the student's CruzID.
    canvas_id: str: the student's Canvas ID (or None).
    uid: str: the student's card UID (or None).
    accesses: list: the student's room accesses (or None).

    Returns True if the student was added, or False if they already exist.
    """
    if (
        limited_data
        or (
            cruzid in student_data["CruzID"].values
            or cruzid in staff_data["CruzID"].values
        )
        or (canvas_id and canvas_id in student_data["Canvas ID"].values)
        or (
            uid
            and (
                uid in student_data["Card UID"].values
                or uid in staff_data["Card UID"].values
            )
        )
    ):
        return False
    student_data.loc[len(student_data)] = [
        first_name,
        last_name,
        cruzid,
        canvas_id if canvas_id else "",
        uid if uid else "",
    ] + ([""] * (len(student_data.columns) - 5))

    if not accesses:
        accesses = [False] * len(rooms)
    set_all_accesses(accesses, cruzid=cruzid)

    return True


def new_staff(first_name, last_name, cruzid, uid=None):
    """
    Add a new staff member to the database (must not be in limited data mode).

    first_name: str: the staff member's first name.
    last_name: str: the staff member's last name.
    cruzid: str: the staff member's CruzID.
    uid: str: the staff member's card UID (or None)

    Returns True if the staff member was added, or False if they already exist.
    """
    if (
        limited_data
        or (
            cruzid in staff_data["CruzID"].values
            or cruzid in student_data["CruzID"].values
        )
        or (
            uid
            and (
                uid in staff_data["Card UID"].values
                or uid in student_data["Card UID"].values
            )
        )
    ):
        return False
    staff_data.loc[len(staff_data)] = [
        uid if uid else "",
        first_name,
        last_name,
        cruzid,
    ]

    return True


def set_uid(cruzid, uid, overwrite=False):
    """
    Set a student or staff's card UID.

    cruzid: str: the CruzID.
    uid: str: the new card UID.
    overwrite: bool: if True, the UID will be overwritten if it already exists.

    Returns the UID if it was set, or the existing UID if it was not set.
    """
    if (
        not cruzid
        or not uid
        or limited_data
        or (
            cruzid not in student_data["CruzID"].values
            and cruzid not in staff_data["CruzID"].values
        )
        or (
            (
                uid in student_data["Card UID"].values
                or uid in staff_data["Card UID"].values
            )
            and not overwrite
        )
    ):
        return False

    if overwrite:
        if uid in student_data["Card UID"].values:
            row = student_data.index[student_data["Card UID"] == uid].tolist()[0]
            student_data.loc[row, "Card UID"] = ""

        if uid in staff_data["Card UID"].values:
            row = staff_data.index[staff_data["Card UID"] == uid].tolist()[0]
            staff_data.loc[row, "Card UID"] = ""

    if is_staff(cruzid=cruzid):
        row = staff_data.index[staff_data["CruzID"] == cruzid].tolist()[0]
        if not staff_data.loc[row, "Card UID"] or overwrite:
            staff_data.loc[row, "Card UID"] = uid
            return staff_data.loc[row, "Card UID"]

    else:
        row = student_data.index[student_data["CruzID"] == cruzid].tolist()[0]
        if not student_data.loc[row, "Card UID"] or overwrite:
            student_data.loc[row, "Card UID"] = uid
            return student_data.loc[row, "Card UID"]


def get_uid(cruzid):
    """
    Get a student's card UID (or False if it doesn't exist).

    cruzid: str: the student's CruzID.

    Returns the student's card UID if it exists, or False if it does not.
    """

    if limited_data or (
        cruzid not in student_data["CruzID"].values
        and cruzid not in staff_data["CruzID"].values
    ):
        return False

    row = student_data.index[student_data["CruzID"] == cruzid].tolist()
    uid = None
    if row:
        row = row[0]
        uid = student_data.loc[row, "Card UID"]

    if not uid:
        row = staff_data.index[staff_data["CruzID"] == cruzid].tolist()
        if row:
            row = row[0]
            uid = staff_data.loc[row, "Card UID"]

    return uid if uid else False


def get_cruzid(uid):
    """
    Get a student or staff's CruzID from their card UID.

    uid: str: the card UID.

    Returns the CruzID if it exists, or False if it does not.
    """

    if limited_data or (
        uid not in student_data["Card UID"].values
        and uid not in staff_data["Card UID"].values
    ):
        return False

    row = student_data.index[student_data["Card UID"] == uid].tolist()
    cruzid = None
    if row:
        row = row[0]
        cruzid = student_data.loc[row, "CruzID"]

    if not cruzid:
        row = staff_data.index[staff_data["Card UID"] == uid].tolist()
        if row:
            row = row[0]
            cruzid = staff_data.loc[row, "CruzID"]

    return cruzid if cruzid else False


def set_access(room, access, cruzid=None, uid=None):
    """
    Set a student's access to a room.

    room: str: the room to set access for.
    access: bool: the access to set.
    cruzid: str: the student's CruzID.
    uid: str: the student's card UID.

    Returns the access if it was set, or None if it was not set.
    """

    if (
        (not cruzid and not uid)
        or (
            not limited_data
            and cruzid
            and student_data.index[student_data["CruzID"] == cruzid].empty
        )
        or (uid and student_data.index[student_data["Card UID"] == uid].empty)
        or (
            not limited_data
            and (
                cruzid
                and uid
                and (
                    student_data.index[student_data["CruzID"] == cruzid].tolist()
                    != student_data.index[student_data["Card UID"] == uid].tolist()
                )
            )
        )
        or room not in rooms
    ):
        return None

    if not limited_data and cruzid:
        row = student_data.index[student_data["CruzID"] == cruzid].tolist()[0]
    elif uid:
        row = student_data.index[student_data["Card UID"] == uid].tolist()[0]
    else:
        return None

    if "Override" not in student_data.loc[row, room]:
        student_data.loc[row, room] = statuses[int(access)]
    else:
        access = bool(
            statuses.index(
                student_data.loc[row, room][
                    (
                        len("Override ")
                        if "Override" in student_data.loc[row, room]
                        else 0
                    ) :
                ]
            )
        )
    return access


def get_access(room, cruzid=None, uid=None):
    """
    Get a student's access to a room.

    room: str: the room to get access for.
    cruzid: str: the student's CruzID.
    uid: str: the student's card UID.
    """

    if (
        (not cruzid and not uid)
        or (
            not limited_data
            and cruzid
            and student_data.index[student_data["CruzID"] == cruzid].empty
        )
        or (uid and student_data.index[student_data["Card UID"] == uid].empty)
        or (
            not limited_data
            and (
                cruzid
                and uid
                and (
                    student_data.index[student_data["CruzID"] == cruzid].tolist()
                    != student_data.index[student_data["Card UID"] == uid].tolist()
                )
            )
        )
    ):
        return None

    if not limited_data and cruzid:
        row = student_data.index[student_data["CruzID"] == cruzid].tolist()[0]
    elif uid:
        row = student_data.index[student_data["Card UID"] == uid].tolist()[0]
    else:
        return False

    access = student_data.loc[row, room]

    return (
        bool(
            statuses.index(access[(len("Override ") if "Override" in access else 0) :])
        )
        if access
        else False
    )


def set_all_accesses(accesses, cruzid=None, uid=None):
    """
    Set a student's access to all rooms.

    accesses: list: the accesses to set.
    cruzid: str: the student's CruzID.
    uid: str: the student's card UID.
    """

    if (
        (not cruzid and not uid)
        or (
            not limited_data
            and cruzid
            and student_data.index[student_data["CruzID"] == cruzid].empty
        )
        or (uid and student_data.index[student_data["Card UID"] == uid].empty)
        or (
            not limited_data
            and (
                cruzid
                and uid
                and (
                    student_data.index[student_data["CruzID"] == cruzid].tolist()
                    != student_data.index[student_data["Card UID"] == uid].tolist()
                )
            )
        )
        or len(accesses) != len(rooms)
    ):
        return False

    if not limited_data and cruzid:
        row = student_data.index[student_data["CruzID"] == cruzid].tolist()[0]
    elif uid:
        row = student_data.index[student_data["Card UID"] == uid].tolist()[0]
    else:
        return False

    for i in range(len(rooms)):
        if "Override" not in student_data.loc[row, rooms[i]]:
            student_data.loc[row, rooms[i]] = statuses[int(accesses[i])]
        else:
            accesses[i] = bool(
                statuses.index(
                    student_data.loc[row, rooms[i]][
                        (
                            len("Override ")
                            if "Override" in student_data.loc[row, rooms[i]]
                            else 0
                        ) :
                    ]
                )
            )
    return accesses


def get_all_accesses(cruzid=None, uid=None):
    """
    Get a student's access to all rooms.

    cruzid: str: the student's CruzID.
    uid: str: the student's card UID.
    """

    if (
        (not cruzid and not uid)
        or (
            not limited_data
            and cruzid
            and student_data.index[student_data["CruzID"] == cruzid].empty
        )
        or (uid and student_data.index[student_data["Card UID"] == uid].empty)
        or (
            not limited_data
            and (
                cruzid
                and uid
                and (
                    student_data.index[student_data["CruzID"] == cruzid].tolist()
                    != student_data.index[student_data["Card UID"] == uid].tolist()
                )
            )
        )
    ):
        return False

    if not limited_data and cruzid:
        row = student_data.index[student_data["CruzID"] == cruzid].tolist()[0]
    elif uid:
        row = student_data.index[student_data["Card UID"] == uid].tolist()[0]
    else:
        return False

    accesses = []
    for room in rooms:
        access = student_data.loc[row, room]
        accesses.append(
            bool(
                statuses.index(
                    access[(len("Override ") if "Override" in access else 0) :]
                )
            )
            if access
            else False
        )

    return accesses


def write_student_sheet():
    global student_sheet_read_len, SEND_BLOCK
    """
    Write the student data to the Google Sheets document.

    Returns True if the data was written, or False if it was not.
    """

    try:
        student_data.sort_values(by=["Last Name"], inplace=True)
        vals = student_data.values.tolist()
        vals.insert(0, student_data.columns.tolist())
        length = len(vals)
        blank_filled = 0

        if student_sheet_read_len > length:
            blank_filled = student_sheet_read_len - length
            vals = vals + [[""] * len(student_data.columns)] * (blank_filled)
        else:
            student_sheet_read_len = length

        for i in range(0, student_sheet_read_len, SEND_BLOCK):
            _ = (
                g_sheets.values()
                .update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=STUDENTS_SHEET
                    + f"!A{i+1}:{str(chr(ord('A') + len(student_data.columns) - 1))}{min(i + SEND_BLOCK, student_sheet_read_len)}",
                    valueInputOption="USER_ENTERED",
                    body={
                        "values": vals[i : min(i + SEND_BLOCK, student_sheet_read_len)]
                    },
                )
                .execute()
            )
        student_sheet_read_len -= blank_filled
        return True
    except HttpError as e:
        logger.error(e)
        return False


def write_staff_sheet():
    global staff_sheet_read_len, SEND_BLOCK
    """
    Write the staff data to the Google Sheets document.
    """

    try:
        staff_data.sort_values(by=["Last Name"], inplace=True)
        vals = staff_data.values.tolist()
        vals.insert(0, staff_data.columns.tolist())
        length = len(vals)

        if staff_sheet_read_len > length:
            vals = vals + [[""] * len(staff_data.columns)] * (
                staff_sheet_read_len - length
            )

        for i in range(0, staff_sheet_read_len, SEND_BLOCK):
            _ = (
                g_sheets.values()
                .update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=STAFF_SHEET
                    + f"!A{i+1}:{str(chr(ord('A') + len(staff_data.columns) - 1))}{min(i + SEND_BLOCK, max(staff_sheet_read_len, length))}",
                    valueInputOption="USER_ENTERED",
                    body={
                        "values": vals[
                            i : min(i + SEND_BLOCK, max(staff_sheet_read_len, length))
                        ]
                    },
                )
                .execute()
            )

        staff_sheet_read_len = length
        return True
    except HttpError as e:
        logger.error(e)
        return False


def write_student_staff_sheets():
    """
    Write the student and staff data to the Google Sheets document.

    Returns True if the data was written, or False if it was not.
    """

    return write_student_sheet() and write_staff_sheet()


def evaluate_modules(completed_modules, cruzid=None, uid=None, num_modules=None):
    """
    Evaluate a student's completed modules and update their room accesses.

    completed_modules: list: the student's completed modules.
    cruzid: str: the student's CruzID.
    uid: str: the student's card UID.
    """

    if not num_modules:
        num_modules = 1000

    for i in range(len(module_data)):
        exp = str(module_data.loc[i, "Modules"])
        exp = exp.lower()
        exp = exp.replace("and", "&")
        exp = exp.replace("or", "|")
        exp = exp.replace(" ", "")

        for m in range(num_modules, 0, -1):
            exp = exp.replace(str(m), "t" if m in completed_modules else "f")

        for l in exp:
            if l not in "tf&|()":
                exp = exp.replace(l, "")

        if (
            set_access(
                module_data.loc[i, "Access Levels"],
                (string_eval(exp) == "t") if len(exp) > 0 else False,
                cruzid=cruzid,
                uid=uid,
            )
            is None
        ):
            return False
    return True


def string_eval(exp):
    """
    Evaluate a string expression for modules.

    exp: str: the expression to evaluate.

    Returns the evaluated expression.
    """

    while "(" in exp:
        start = 0
        end = exp.index(")")
        while "(" in exp[start + 1 : end]:
            start = exp.index("(", start + 1)

        exp = exp[:start] + string_eval(exp[start + 1 : end]) + exp[end + 1 :]

    while "&" in exp:
        i = exp.index("&")

        bw = 0
        if "|" in exp[:i]:
            bw = exp.rindex("|", 0, i) + 1

        fw = len(exp)
        if "|" in exp[i + 1 :]:
            fw = exp.index("|", i + 1)

        exp = (
            exp[:bw]
            + (
                "t"
                if (
                    string_eval(exp[bw:i]) == "t"
                    and string_eval(exp[i + 1 : fw]) == "t"
                )
                else "f"
            )
            + exp[fw:]
        )

    while "|" in exp:
        i = exp.index("|")
        exp = (
            exp[: i - 1]
            + (
                "t"
                if (string_eval(exp[:i]) == "t" or string_eval(exp[i + 1 :]) == "t")
                else "f"
            )
            + exp[i + 2 :]
        )

    return exp


def is_staff(cruzid=None, uid=None):
    """
    Check if a student is a staff member.

    cruzid: str: the student's CruzID.
    uid: str: the student's card UID.
    """

    return bool(
        (not limited_data and cruzid and cruzid in staff_data["CruzID"].values)
        or (uid and uid in staff_data["Card UID"].values)
    )


def get_user_data(cruzid=None, uid=None):
    """
    Get a user's data.

    cruzid: str: the user's CruzID.
    uid: str: the user's card UID.

    Returns an array with the user's data if they exist, or None if they do not. The array is as follows:
    [is_staff, cruzid, uid, first_name, last_name, access1, access2, ..., accessN]
    Where access1, access2, ..., accessN are the user's room accesses, corresponding to the rooms list.
    """
    if student_exists(cruzid=cruzid, uid=uid):
        if cruzid:
            uid = student_data.loc[student_data["CruzID"] == cruzid, "Card UID"].values[
                0
            ]
        elif uid:
            cruzid = student_data.loc[student_data["Card UID"] == uid, "CruzID"].values[
                0
            ]
        return (
            [False, cruzid, uid]
            + student_data.loc[
                (
                    student_data["CruzID"] == cruzid
                    if cruzid
                    else student_data["Card UID"] == uid
                ),
                "First Name":"Last Name",
            ].values.tolist()[0]
            # + get_all_accesses(cruzid=cruzid, uid=uid)
        )
    elif is_staff(cruzid=cruzid, uid=uid):
        if cruzid:
            uid = staff_data.loc[
                staff_data["CruzID"] == cruzid, "Card UID"
            ].values.tolist()[0]
        elif uid:
            cruzid = staff_data.loc[
                staff_data["Card UID"] == uid, "CruzID"
            ].values.tolist()[0]

        return [True, cruzid, uid] + staff_data.loc[
            (
                staff_data["CruzID"] == cruzid
                if cruzid
                else staff_data["Card UID"] == uid
            ),
            "First Name":"Last Name",
        ].values.tolist()[0]
    else:
        return (None, None)


def remove_student(cruzid):
    """
    Remove a student from the database.

    cruzid: str: the student's CruzID.

    Returns True if the student was removed, or False if they do not exist.
    """

    if not student_exists(cruzid=cruzid):
        return False

    row = student_data.index[student_data["CruzID"] == cruzid].tolist()[0]
    student_data.drop(row, inplace=True)  # TODO: move to archive sheet instead
    student_data.reset_index(drop=True, inplace=True)

    return True


def remove_staff(cruzid):
    """
    Remove a staff member from the database.

    cruzid: str: the staff member's CruzID.

    Returns True if the staff member was removed, or False if they do not exist.
    """

    if not is_staff(cruzid=cruzid):
        return False

    row = staff_data.index[staff_data["CruzID"] == cruzid].tolist()[0]
    staff_data.drop(row, inplace=True)  # TODO: move to archive sheet instead
    staff_data.reset_index(drop=True, inplace=True)

    return True


def clamp_staff(staff_list):
    """
    Clamp the staff list to the staff sheet.

    staff_list: list: the list of staff members to clamp.

    Returns the clamped staff list.
    """

    # return [
    #     staff_list[i]
    #     for i in range(len(staff_list))
    #     if staff_list[i] in staff_data["CruzID"].values
    # ]

    for i in range(staff_data.shape[0]):
        if staff_data.loc[i, "CruzID"] not in staff_list:
            staff_data.drop(i, inplace=True)  # TODO: move to archive sheet instead


def clamp_students(student_list):
    """
    Clamp the student list to the student sheet.

    student_list: list: the list of students to clamp.

    Returns the clamped student list.
    """

    for i in range(student_data.shape[0]):
        if student_data.loc[i, "CruzID"] not in student_list:
            student_data.drop(i, inplace=True)  # TODO: move to archive sheet instead


def log(uid, access, alarm_status, disarm_time):
    """
    Log a card read.

    uid: str: the card UID.
    access: str: the highest valid room access read at this reader.
    alarm_status: bool: the alarm status (True if the alarm was triggered, False if it was not).
    disarm_time: str: the amount of time the alarm was disarmed for.

    Returns True if the data was logged, or False if it was not.
    """

    try:
        _ = (
            g_sheets.values()
            .append(
                spreadsheetId=SPREADSHEET_ID,
                range=LOG_SHEET,
                valueInputOption="USER_ENTERED",
                body={
                    "values": [
                        [
                            str(datetime.datetime.now()),
                            uid,
                            reader_id,
                            "",  # location
                            "",  # first name
                            "",  # last name
                            "",  # cruzid
                            access,
                            "Triggered" if alarm_status else "Not Triggered",
                            disarm_time,
                        ]
                    ]
                },
            )
            .execute()
        )
        return True
    except HttpError as e:
        logger.error(e)
        return False


def alarm_setting():
    """
    Get the alarm setting from the reader data.

    Returns the alarm enabled/disabled state and alarm delay time in minutes.
    """

    return this_reader["alarm"], this_reader["alarm_delay_min"]


def need_updating():
    """
    Check if this Pi/Zero needs updating.

    Returns True if update needed, or False if not.
    """

    return this_reader and (
        this_reader["needs_update"] == "PENDING" or this_reader["needs_update"] == True
    )


def scan_uid(uid, alarm_status=False):
    """
    Return the LED color and alarm delay time for a given card UID.

    uid: str: the card UID.
    alarm_status: bool: the alarm status (True if the alarm was triggered, False if it was not).

    Returns the LED hex color and alarm delay time in minutes, or False if no "No Access" value is specified, or None if the uid does not exist.
    """

    if is_staff(uid=uid):
        if access_data["staff"]:
            if ENABLE_SCAN_LOGS:
                log(uid, "Staff", alarm_status, access_data["staff"][1])
            return access_data["staff"]
        elif access_data["no_access"]:
            if ENABLE_SCAN_LOGS:
                log(uid, "Staff", alarm_status, access_data["no_access"][1])
            return access_data["no_access"]
        else:
            if ENABLE_SCAN_LOGS:
                log(uid, "Staff (Not Found)", alarm_status, 0)
            return False

    elif student_exists(uid=uid):
        for i in range(len(rooms)):
            if get_access(rooms[i], uid=uid) and access_data[rooms[i]]:
                if ENABLE_SCAN_LOGS:
                    log(uid, rooms[i], alarm_status, access_data[rooms[i]][1])
                return access_data[rooms[i]]

        if access_data["no_access"]:
            if ENABLE_SCAN_LOGS:
                log(uid, "No Access", alarm_status, access_data["no_access"][1])
            return access_data["no_access"]
        else:
            if ENABLE_SCAN_LOGS:
                log(uid, "Student (Not Found)", alarm_status, 0)
            return False

    else:
        if ENABLE_SCAN_LOGS:
            log(uid, "Unknown", alarm_status, 0)
        return access_data["no_access"]


def run_in_thread(
    f: Callable, args: Iterable[Any] = (), kwargs: Mapping[str, Any] = None
):
    """
    Run a function in a separate thread.

    f: function: the function to run.
    """

    Thread(target=f, args=args, kwargs=kwargs).start()


def update_all_readers():
    """
    Set all readers to need updating.
    """

    try:
        _ = (
            g_sheets.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=READERS_SHEET + f"!G3:G{len(reader_data)+1}",
                valueInputOption="USER_ENTERED",
                body={"values": [["PENDING"]] * (len(reader_data) - 1)},
            )
            .execute()
        )
    except HttpError as e:
        logger.error(e)


def update_reader(id, location=None, alarm=None, alarm_delay=None):
    """
    Set a reader to need updating.

    id: int: the reader ID.
    """

    if id < 0 or id >= len(reader_data):
        return False

    if not set_reader_properties(id, location, alarm, alarm_delay):
        return False

    try:
        _ = (
            g_sheets.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=READERS_SHEET + f"!G{id+2}",
                valueInputOption="USER_ENTERED",
                body={"values": [["PENDING"]]},
            )
            .execute()
        )
    except HttpError as e:
        logger.error(e)
        return False

    return True


def get_alarm_status(id):
    """
    Get the alarm status of a reader.

    id: int: the reader ID.

    Returns the reader's alarm status, or None if the reader does not exist.
    """

    if id < 0 or id >= len(reader_data):
        return None

    return reader_data.loc[id, "alarm_status"]


def get_all_alarm_statuses():
    """
    Get the alarm status of all readers.

    Returns a list of reader alarm statuses.
    """

    return reader_data["alarm_status"].tolist()


def set_reader_properties(id, location=None, alarm=None, alarm_delay=None):
    """
    Set the properties of a reader.

    id: int: the reader ID.
    location: str: the reader's location.
    alarm: bool: the alarm status.
    alarm_delay: int: the alarm delay time in minutes.
    """

    if id < 0 or id >= len(reader_data):
        return False

    if location:
        reader_data.loc[id, "location"] = location
    if alarm:
        reader_data.loc[id, "alarm"] = alarm
    if alarm_delay:
        reader_data.loc[id, "alarm_delay_min"] = alarm_delay

    try:
        _ = (
            g_sheets.values()
            .update(
                spreadsheetId=SPREADSHEET_ID,
                range=READERS_SHEET + f"!C{id+2}:E{id+2}",
                valueInputOption="USER_ENTERED",
                body={"values": [[location, alarm, alarm_delay]]},
            )
            .execute()
        )
        return True
    except HttpError as e:
        logger.error(e)
        return False


if __name__ == "__main__":
    # Create a new directory for logs if it doesn't exist
    if not os.path.exists(path + "/logs/sheet"):
        os.makedirs(path + "/logs/sheet")

    # create new logger with all levels
    root_logger = logging.getLogger("root")
    root_logger.setLevel(logging.DEBUG)

    # create file handler which logs debug messages (and above - everything)
    fh = logging.FileHandler(f"logs/sheet/{str(datetime.datetime.now())}.log")
    fh.setLevel(logging.DEBUG)

    # create console handler which only logs warnings (and above)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)

    # create formatter and add it to the handlers
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s: %(message)s")
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    # add the handlers to the logger
    root_logger.addHandler(fh)
    root_logger.addHandler(ch)
    get_sheet_data(limited=False)
    # print(student_data)
    # print()
    # print(staff_data)
    # print()
    # print(access_data)
    # print()
    # # print(module_data)
    # # print()
    # print(reader_data)
    # print()
    # print(this_reader)
    # print()

    # print(alarm_enabled, alarm_delay)
    # print(alarm_setting())
    # print(reader_need_updating())

    # update_all_readers()
    # print(get_alarm_status(1))
    # # print(get_all_reader_statuses())
    # check_in()

    # log("Canvas", "", False, 0)

    # print(scan_uid("63B104FF"))
    # print(scan_uid("73B104FF"))
    # print(scan_uid("83B104FF"))
    # print(scan_uid("93B104FF"))

    # student_exists()

    # print time
    # print(str(datetime.datetime.now()))

    # print(get_uid("ewachtel"))
    # print(get_uid("jowemorr"))

    # if not new_student("Cédric", "Chartier", "cchartie"):
    #     print("CruzID, Canvas ID, or Card UID already in use.")

    # print(set_uid("sabsadik", "23458923", overwrite=True))
    # remove_student("sabsadik")

    # print(staff_data)
    # remove_staff("imadan0")
    # new_staff("Ethan", "Wachtel", "asdf", "63B104FF")
    # new_staff("Cédric", "Chartier", "fdsa", "01234567")
    # new_staff("Cédric", "Chartier", "nobody")
    # remove_staff("asdf")
    # remove_staff("fdsa")
    # remove_staff("nobody")
    # print(staff_data)

    # print(set_access("BE-49", False, cruzid="tstudent"))
    # print(get_access("BE-49", uid="0123456789"))
    # print(get_access("BE-49", cruzid="tstudent"))
    # print(set_all_accesses([True] * len(rooms), cruzid="tstudent"))
    # print(get_all_accesses(cruzid="tstudent"))
    # print(evaluate_modules([1, 2, 5, 6, 7, 8, 9, 10], cruzid="tstudent"))

    # write_student_sheet()
    # write_staff_sheet()

    # set_uid("ewachtel", "63B104FF")
    # set_uid("cchartie", "63B104FF")
    # write_student_staff_sheets()

    # log("63B104FF", "Staff", True, 10)

    # print(get_user_data(uid="63B104FF"))
    # print(get_user_data(uid="73B104FF"))
    # print(get_user_data(uid="83B104FF"))
    # print(get_user_data(cruzid="ewachtel"))
    # print(get_user_data(cruzid="cchartie"))

    # print(
    #     student_data.loc[
    #         student_data["3D Printing"].isin(["Access", "Override Access"])
    #     ]["CruzID"].values.tolist()
    # )
    # print(staff_data["CruzID"].values.tolist())

    # print()
    # print(student_data)
    # print()
    # print(staff_data)
    # print()
    # print(access_data)
    # print()
    # print(module_data)
    # print()
    # print(reader_data)
