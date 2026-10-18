# -*- coding: utf-8 -*-
"""The notifications the lab actually receives, as html2plaintext hands them over.
Taken from real mails (2026-10-11), names included: these are the shapes the parsers
have to keep reading."""

ALLIEDSTAR_SUBJECT = "Fwd: [AS Connect] New Order Notification"
ALLIEDSTAR_BODY = """---------- Forwarded message ---------
From: <cloud@allied-star.com>
Date: Sat, 10 Oct 2026 at 16:24
Subject: [AS Connect] New Order Notification
To: <lab.scans@example.com>

Alliedstar

Hello,

Arabian Dental Lab has received a new order (261010025423947624448) from your partner Dr Meera's centre for prosthodontics and implant. Please log in to AS Connect for details.

More Details
https://connect.allied-star.com/orders/261010025423947624448

This is an automated email. Please do not reply to this email.
If you have any questions about our services, please contact us by email to support@allied-star.com.
"""

WETRANSFER_SUBJECT = "Fwd: rubydoctor@example.com sent you KIRAN FIXED ANTERIOR BITE PLATE via WeTransfer"
WETRANSFER_BODY = """---------- Forwarded message ---------
From: WeTransfer <noreply@wetransfer.com>
Date: Sat, 10 Oct 2026 at 16:58
Subject: rubydoctor@example.com sent you KIRAN FIXED ANTERIOR BITE PLATE via WeTransfer
To: <lab.scans@example.com>

WeTransfer

rubydoctor@example.com sent you KIRAN FIXED ANTERIOR BITE PLATE

4 items, 52.2 MB in total • Expires on 13 October, 2026

Get your files

Download link
https://wetransfer.com/downloads/0000000000000000000000000000000020261010112749/1111111111111111111111111111111120261010112803/263884?t_exp=1791890869

4 items
bite1.stl
4.17 MB
bite2.stl
2.72 MB
lower.stl
16.7 MB
upper.stl
28.6 MB
"""

FREQTY_SUBJECT = "Fwd: Receive case notifications"
FREQTY_BODY = """---------- Forwarded message ---------
From: Freqty <freqty_cloud@freqtek.com>
Date: Wed, 7 Oct 2026 at 17:11
Subject: Receive case notifications
To: BRIGHTSMILE TOOTH AFFAIR PRIVATE LTD VKM001017 <lab.scans@example.com>

File Information

lab_2026-10-07_165237
Source: BRIGHTSMILE TOOTH AFFAIR PRIVATE LTD VKM001017
Size: 20.13MB
FirstUpTime: 2026-10-07 17:10:42

Patient Information

Name: DIYA S NAIR
Gender: unknown
Age: 0
Notes:

Dentist Information

Name: JOHN MATHEW
Clinic: BRIGHTSMILE TOOTH AFFAIR PRIVATE LTD VKM001017
Phone:
Notes:

Case Information

OrderCreateTime: 2026/10/07-16:52:37
Notes: URP WITH MILD AIBP PLS CALL DR KAVYA

view detail
https://cloud.freqtek.com/case/view?id=165237
"""

DEXIS_SUBJECT = "Fwd: [Dr.Arun K R]Incoming request(OJF-5570)"
DEXIS_BODY = """---------- Forwarded message ---------
From: DEXIS IOS Cloud <no_reply@dexis.com>
Date: Mon, 5 Oct 2026 at 12:10
Subject: [Dr.Arun K R]Incoming request(OJF-5570)
To: <lab.scans@example.com>

DEXIS IS Connect

Incoming request

Hello,

Practitioner Dr.Arun K R [arun.pm.dentist@example.com] submitted case OJF-5570.

You can download the case data, available for 365 days, by clicking the link below:
https://dentalconnect.dexis.com/main.php?action=view_case&k=4488928&email=lab.scans%40example.com
"""

UNKNOWN_SUBJECT = "Fwd: scan for Rahul"
UNKNOWN_BODY = """---------- Forwarded message ---------
From: Dr Priya <priya.dental@example.com>

please find the scan attached, upper hawley
"""

WHATSAPP_RAHIM = "Patient name - Rahim VP\nUpper &Lower Hawley's retainer\nDelivery -13-10-26"
WHATSAPP_AYESHA = "Patient name - Ayesha Nadia. P\nUpper Exis Retainer 2set\nDelivery -13-10-26"
WHATSAPP_ARJUN = "long band n loop for arjun mehta"
WHATSAPP_SAMEER = "sameer verma lingual arch plain plain bands on 36 46"
WHATSAPP_TANVI = "Tanvi upper Bilateral expansion"

# How the same messages leave WhatsApp, in its three shapes.
CHAT_WEB_COPY = """[12:36 pm, 11/10/2026] ANJALI MENON K: Patient name - Rahim VP
Upper &Lower Hawley's retainer
Delivery -13-10-26
[4:02 pm, 11/10/2026] +91 90000 12345: Patient name - Ayesha Nadia. P
Upper Exis Retainer 2set
Delivery -13-10-26"""

CHAT_ANDROID_EXPORT = (
    "11/10/26, 12:36\u202fpm - ~\u202fANJALI MENON K: Patient name - Rahim VP\n"
    "Upper &Lower Hawley's retainer\n"
    "Delivery -13-10-26\n"
    "11/10/26, 12:37\u202fpm - ~\u202fANJALI MENON K: Rahim P LowerJawScan.stl (file attached)\n"
    "11/10/26, 12:37\u202fpm - ~\u202fANJALI MENON K: Rahim P UpperJawScan.stl (file attached)\n"
    "11/10/26, 6:06\u202fpm - KidsDentalClinic: do u have double arm baby rme screw 9mm for expansion ??")

CHAT_IOS_EXPORT = ("\u200e[06/10/26, 10:49:12 AM] KidsDentalClinic: \u200e<attached: 00000041-2026 scan.zip>\n"
                   "sameer verma lingual arch plain plain bands on 36 46")

# Share notifications from the file-transfer platforms, in their standard wording.
# Modelled on each platform's notification template - not captured from the lab's
# own mailbox like the ones above - so a real one forwarded later should be added.
DRIVE_SUBJECT = 'Fwd: Folder shared with you: "Rahim VP"'
DRIVE_BODY = """---------- Forwarded message ---------
From: Dr Arun K R (via Google Drive) <drive-shares-dm-noreply@google.com>
Date: Mon, 12 Oct 2026 at 10:15
Subject: Folder shared with you: "Rahim VP"
To: <lab.scans@example.com>

Dr Arun K R shared a folder
Dr Arun K R (arun.pm.dentist@example.com) has invited you to contribute to the following shared folder:
Rahim VP
Patient name - Rahim VP
Upper Hawley retainer
Delivery -16-10-26
Open
https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing_eil_m
Google LLC, 1600 Amphitheatre Parkway, Mountain View, CA 94043, USA
You have received this email because arun.pm.dentist@example.com shared a folder with you from Google Drive.
https://support.google.com/drive
"""

DROPBOX_SUBJECT = 'Fwd: Dr Priya Nair shared "Diya scans" with you'
DROPBOX_BODY = """---------- Forwarded message ---------
From: Dropbox <no-reply@dropbox.com>
Subject: Dr Priya Nair shared "Diya scans" with you

Dr Priya Nair (priya.dental@example.com) shared "Diya scans" with you.
View folder
https://www.dropbox.com/scl/fo/a1b2c3/AAAbbbCCC?rlkey=xyz&dl=0
https://www.dropbox.com/help
"""

ONEDRIVE_SUBJECT = 'Fwd: Dr Jose shared "Tanvi expansion" with you'
ONEDRIVE_BODY = """---------- Forwarded message ---------
From: Dr Jose <no-reply@sharepointonline.com>
Subject: Dr Jose shared "Tanvi expansion" with you

Dr Jose shared a folder with you
Here's the folder that Dr Jose shared with you.
Tanvi expansion
Open
https://1drv.ms/f/s!AbCdEfGh12345
"""
