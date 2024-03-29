from datetime import datetime, timedelta
import random
from multiprocessing import Process, Queue
from time import sleep

import fake_nfc as nfc

# import reader_nfc as nfc
import sheet

SHEET_UPDATE_HOUR = 4  # 4am
CHECKIN_TIMEOUT = 30  # 30 seconds

alarm_status = False

if __name__ == "__main__":
    sheet.get_sheet_data(limited=True)
    sheet.check_in(alarm_status=alarm_status)
    try:
        while True:
            if (
                not sheet.last_update_date
                or datetime.now().date() > sheet.last_update_date
            ) and datetime.now().hour >= SHEET_UPDATE_HOUR:
                print("Updating sheet...")
                sheet.get_sheet_data()
                sheet.check_in(alarm_status=alarm_status)
            elif (
                not sheet.last_checkin_time
                or datetime.now() - sheet.last_checkin_time
                > timedelta(0, CHECKIN_TIMEOUT, 0, 0, 0, 0, 0)
            ):
                print("Checking in...")
                sheet.check_in(alarm_status=alarm_status)

            print("Hold a tag near the reader")
            card_id = nfc.read_card_queue_timeout(10)
            print(card_id)
            if card_id:
                response = sheet.scan_uid(card_id)
                if not response:
                    print("error - card not in database or something else")
                    pass
                else:
                    color, timeout = response
                    print(color, timeout)
            else:
                print("error - scanned too soon or not scanned")
    except KeyboardInterrupt:
        nfc.close()
        raise
