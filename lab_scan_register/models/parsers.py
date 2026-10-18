# -*- coding: utf-8 -*-
"""Reading a scan request out of the text it arrived in.

Pure functions, no ORM: a WhatsApp caption or a scanner-cloud e-mail goes in, a dict
of what could be read out of it comes out. Nothing is guessed - a key is present only
when the text actually said it, so a field the reader could not settle stays empty for
a person to fill rather than wrong.

Each e-mail platform has its own shape, taken from real notifications:

* Allied Star (AS Connect): "X has received a new order (NUMBER) from your partner
  CLINIC. Please log in to AS Connect for details."
* WeTransfer: "SENDER sent you TITLE via WeTransfer", "4 items, 52.2 MB in total •
  Expires on 13 October, 2026", then the file names.
* Freqty: labelled sections - File / Patient / Dentist / Case Information - each a run
  of "Key: value" lines.
* DEXIS IS Connect: "[Dr.NAME]Incoming request(CASE)", "Practitioner NAME [EMAIL]
  submitted case CASE", "available for 365 days".

The lab forwards these from its own mailbox, so the sender Odoo sees is the lab; the
real sender is the "From:" line inside the forwarded body.
"""
import re
from datetime import date, timedelta

from dateutil import parser as dateparser

FWD_PREFIX = re.compile(r'^\s*(?:(?:fwd?|fw|re|tr)\s*:\s*)+', re.I)
FWD_FROM = re.compile(r'^\s*From:\s*(?P<from>.+?)\s*$', re.I | re.M)
EMAIL_RX = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
URL_RX = re.compile(r'https?://[^\s<>"\')\]]+')
FILE_RX = re.compile(r'\b[\w.() -]+?\.(?:stl|ply|obj|zip|rar|7z|dcm|pdf)\b', re.I)
WS = re.compile(r'\s+')


def _clean(value):
    return WS.sub(' ', value or '').strip(' .\t\r\n-:')


def strip_subject(subject):
    return FWD_PREFIX.sub('', subject or '').strip()


def forwarded_from(text):
    """The real sender of a forwarded mail, from the first 'From:' line of the body."""
    match = FWD_FROM.search(text or '')
    return _clean(match.group('from')) if match else ''


def first_email(text):
    match = EMAIL_RX.search(text or '')
    return match.group(0).lower() if match else ''


def first_url(text, containing=None):
    for url in URL_RX.findall(text or ''):
        if not containing or containing in url.lower():
            return url.rstrip('.,;')
    return ''


def parse_date(value, dayfirst=True):
    """A date out of '13-10-26', '13/10/2026', '13 October, 2026' or a timestamp."""
    if not value:
        return None
    try:
        return dateparser.parse(value, dayfirst=dayfirst, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


# ---------------------------------------------------------------------- WhatsApp
PATIENT_RX = re.compile(
    r'^\s*(?:patient(?:\s*name)?|pt\.?(?:\s*name)?|name)\s*[-:–]\s*(?P<v>.+?)\s*$', re.I)
DELIVERY_RX = re.compile(
    r'^\s*(?:delivery|deliver|due|required|needed|wanted)(?:\s*(?:date|by|on))?\s*[-:–]?\s*'
    r'(?P<v>\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}.*)$', re.I)
FOR_RX = re.compile(r'^(?P<app>.+?)\s+for\s+(?P<pt>[A-Za-z][A-Za-z .]{2,})$', re.I)


def parse_whatsapp(text):
    """What a WhatsApp caption says, the way the clinics actually write them:

        Patient name - Rahim VP
        Upper &Lower Hawley's retainer
        Delivery -13-10-26

    or "long band n loop for arjun mehta". Lines that are neither the patient nor
    the delivery date are what is being asked for.
    """
    out = {}
    rest = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        match = PATIENT_RX.match(line)
        if match and 'patient' not in out:
            out['patient'] = _clean(match.group('v'))
            continue
        match = DELIVERY_RX.match(line)
        if match and 'delivery' not in out:
            parsed = parse_date(match.group('v'))
            if parsed:
                out['delivery'] = parsed
                continue
        rest.append(line)
    if rest:
        body = ' / '.join(rest)
        if 'patient' not in out and len(rest) == 1:
            match = FOR_RX.match(rest[0])
            if match:
                out['patient'] = _clean(match.group('pt')).title()
                body = _clean(match.group('app'))
        out['appliance'] = body
    return out


# ---------------------------------------------------------------------- e-mail
def _alliedstar(subject, text):
    out = {'platform': 'alliedstar'}
    match = re.search(r'new order\s*\((?P<ref>[\w-]+)\)\s*from your partner\s*(?P<clinic>.+?)'
                      r'\s*(?:\.\s*)?Please log in', text, re.I | re.S)
    if match:
        out['external_ref'] = match.group('ref')
        out['clinic_name'] = _clean(match.group('clinic'))
    out['link'] = first_url(text, 'allied') or first_url(text)
    return out


def _wetransfer(subject, text):
    out = {'platform': 'wetransfer'}
    match = re.search(r'(?P<sender>\S+@\S+)\s+sent you\s+(?P<title>.+?)\s+via WeTransfer',
                      subject or '', re.I)
    if not match:
        match = re.search(r'(?P<sender>\S+@\S+)\s+sent\s+you\s+(?P<title>.+?)\s*$',
                          ' '.join(text.split('\n')[:40]), re.I)
    if match:
        out['doctor_email'] = match.group('sender').strip('<>').lower()
        out['appliance'] = _clean(match.group('title'))
    match = re.search(r'(?P<n>\d+)\s+items?,\s*(?P<size>[\d.,]+\s*[KMG]B)\s+in total'
                      r'.*?Expires on\s+(?P<exp>[^\n•·]+)', text, re.I | re.S)
    if match:
        parsed = parse_date(match.group('exp'))
        if parsed:
            out['link_expires'] = parsed
        out['file_names'] = []
    files = [_clean(f) for f in FILE_RX.findall(text)]
    if files:
        out['file_names'] = list(dict.fromkeys(files))
    out['link'] = first_url(text, 'wetransfer.com/downloads') or first_url(text, 'wetransfer')
    return out


def _section(text, heading):
    """The run of text from a heading to the next blank-line-separated heading."""
    match = re.search(re.escape(heading) + r'\s*\n(?P<body>.*?)(?=\n\s*\n\s*[A-Z][A-Za-z ]+ Information\s*\n|\Z)',
                      text, re.S)
    return match.group('body') if match else ''


def _kv(block, key):
    match = re.search(r'^\s*' + re.escape(key) + r'\s*:\s*(?P<v>.*?)\s*$', block, re.I | re.M)
    return _clean(match.group('v')) if match else ''


def _freqty(subject, text):
    out = {'platform': 'freqty'}
    patient = _section(text, 'Patient Information')
    dentist = _section(text, 'Dentist Information')
    case = _section(text, 'Case Information')
    files = _section(text, 'File Information')
    if _kv(patient, 'Name'):
        out['patient'] = _kv(patient, 'Name')
    if _kv(dentist, 'Name'):
        out['doctor_name'] = _kv(dentist, 'Name')
    if _kv(dentist, 'Clinic'):
        out['clinic_name'] = _kv(dentist, 'Clinic')
    notes = _kv(case, 'Notes') or _kv(patient, 'Notes')
    if notes:
        out['notes'] = notes
    match = re.search(r'\b([a-z][a-z0-9]*_\d{4}-\d{2}-\d{2}_\d+)\b', text) or \
        re.search(r'^\s*(?P<f>[\w.-]+)\s*$', files, re.M)
    if match:
        out['external_ref'] = match.group(1) if match.lastindex else match.group('f')
    out['link'] = first_url(text)
    return out


def _dexis(subject, text):
    out = {'platform': 'dexis'}
    match = re.search(r'\[(?P<doc>[^\]]+)\]\s*Incoming request\s*\((?P<ref>[^)]+)\)',
                      subject or '', re.I)
    if match:
        out['doctor_name'] = _clean(match.group('doc'))
        out['external_ref'] = _clean(match.group('ref'))
    match = re.search(r'Practitioner\s+(?P<doc>.+?)\s*\[(?P<mail>[^\]]+)\]\s*submitted case\s+'
                      r'(?P<ref>[\w-]+)', text, re.I)
    if match:
        out['doctor_name'] = _clean(match.group('doc'))
        out['doctor_email'] = match.group('mail').strip().lower()
        out['external_ref'] = match.group('ref')
    match = re.search(r'available for\s+(?P<days>\d+)\s+days', text, re.I)
    if match:
        out['link_expires'] = date.today() + timedelta(days=int(match.group('days')))
    out['link'] = first_url(text, 'dexis') or first_url(text)
    return out


def all_urls(text):
    return [u.rstrip('.,;') for u in URL_RX.findall(text or '')]


def _typed_note(text):
    """A patient and a delivery date the sender typed into the message, in the same
    shapes the clinics use on WhatsApp ("Patient name - X", "Delivery -16-10-26")."""
    out = {}
    for raw in (text or '').splitlines():
        line = raw.strip()
        match = PATIENT_RX.match(line)
        if match and 'patient' not in out and not line.lower().startswith('name'):
            out['patient'] = _clean(match.group('v'))
            continue
        match = DELIVERY_RX.match(line)
        if match and 'delivery' not in out:
            parsed = parse_date(match.group('v'))
            if parsed:
                out['delivery'] = parsed
    return out


def _sharer(text, subject):
    """'Dr X (dr.x@mail.com) shared/has invited you ...' - the person, not the platform."""
    out = {}
    match = re.search(r'^\s*(?P<name>[^\n()]{2,80}?)\s*\((?P<mail>[^()\s]+@[^()\s]+)\)\s+'
                      r'(?:has\s+)?(?:shared|invited)', text, re.I | re.M)
    if match:
        out['doctor_name'] = _clean(match.group('name'))
        out['doctor_email'] = match.group('mail').strip().lower()
    return out


def _google_drive(subject, text):
    """Google Drive share notifications: 'Folder shared with you: "X"' or
    '"X" - Invitation to view', from "Name (via Google Drive)"."""
    out = {'platform': 'google_drive'}
    match = (re.search(r'(?:folder|item|file|document)s?\s+shared with you:\s*"(?P<t>[^"]+)"', subject or '', re.I)
             or re.search(r'^"?(?P<t>.+?)"?\s*-\s*invitation to (?:view|edit|comment)', subject or '', re.I))
    if match:
        out['appliance'] = _clean(match.group('t'))
    out.update(_sharer(text, subject))
    if 'doctor_name' not in out:
        match = re.search(r'From:\s*"?(?P<name>[^"<\n]+?)\s*\(via Google Drive\)', text, re.I)
        if match:
            out['doctor_name'] = _clean(match.group('name'))
    out.update(_typed_note(text))
    out['link'] = first_url(text, 'drive.google.com') or first_url(text, 'docs.google.com')
    return out


def _dropbox(subject, text):
    """Dropbox: 'Name shared "X" with you', from no-reply@dropbox.com."""
    out = {'platform': 'dropbox'}
    match = re.search(r'^(?P<name>.+?)\s+shared\s+"(?P<t>[^"]+)"\s+with you', subject or '', re.I)
    if match:
        out['doctor_name'] = _clean(match.group('name'))
        out['appliance'] = _clean(match.group('t'))
    out.update({k: v for k, v in _sharer(text, subject).items() if k == 'doctor_email' or 'doctor_name' not in out})
    out.update(_typed_note(text))
    out['link'] = first_url(text, 'dropbox.com/scl') or first_url(text, 'dropbox.com')
    return out


def _onedrive(subject, text):
    """OneDrive / SharePoint: 'Name shared "X" with you'."""
    out = {'platform': 'onedrive'}
    match = re.search(r'^(?P<name>.+?)\s+shared\s+"(?P<t>[^"]+)"\s+with you', subject or '', re.I)
    if match:
        out['doctor_name'] = _clean(match.group('name'))
        out['appliance'] = _clean(match.group('t'))
    out.update({k: v for k, v in _sharer(text, subject).items() if k == 'doctor_email' or 'doctor_name' not in out})
    out.update(_typed_note(text))
    out['link'] = (first_url(text, '1drv.ms') or first_url(text, 'onedrive.live.com')
                   or first_url(text, 'sharepoint.com'))
    return out


def _other(subject, text):
    out = {'platform': 'other'}
    if subject:
        out['appliance'] = _clean(subject)
    lines = [l.strip() for l in (text or '').splitlines() if l.strip()]
    if lines:
        out['notes'] = '\n'.join(lines)[:600]
    link = first_url(text)
    if link:
        out['link'] = link
    out.update(_typed_note(text))
    return out


PLATFORMS = (
    ('alliedstar', lambda hay: 'allied-star' in hay or 'as connect' in hay, _alliedstar),
    ('wetransfer', lambda hay: 'wetransfer' in hay, _wetransfer),
    ('freqty', lambda hay: 'freqtek' in hay or 'freqty' in hay
               or 'receive case notification' in hay, _freqty),
    ('dexis', lambda hay: 'dexis' in hay, _dexis),
    ('google_drive', lambda hay: 'drive-shares' in hay or 'via google drive' in hay, _google_drive),
    ('dropbox', lambda hay: 'dropbox' in hay, _dropbox),
    ('onedrive', lambda hay: 'onedrive' in hay or 'sharepoint' in hay, _onedrive),
)


def parse_email(subject, text, email_from=''):
    """Everything readable from a notification: which platform, who sent it, and what
    it said about the patient, the doctor, the case and the download."""
    subject = strip_subject(subject)
    text = text or ''
    sender = forwarded_from(text) or email_from or ''
    hay = ' '.join((sender, subject)).lower()
    for platform, matches, parse in PLATFORMS:
        if matches(hay):
            out = parse(subject, text)
            break
    else:
        out = _other(subject, text)
    out['sender'] = sender
    if 'doctor_email' not in out:
        mail = first_email(sender)
        if mail and out['platform'] == 'other':
            out['doctor_email'] = mail
    out['subject'] = subject
    # a folder named after the patient says nothing more as a description
    if out.get('appliance') and out.get('patient') and out['appliance'].lower() == out['patient'].lower():
        del out['appliance']
    return {k: v for k, v in out.items() if v not in (None, '', [])}


# ---------------------------------------------------------------------- WhatsApp chats
# What WhatsApp hands over when messages leave it, in its three shapes:
#
#   copied in WhatsApp Web / Desktop   [12:36 pm, 11/10/2026] ANJALI MENON K: Patient name - …
#   exported on Android                11/10/26, 12:36 pm - ANJALI MENON K: Patient name - …
#   exported on iPhone                 [11/10/26, 12:36:15 PM] ANJALI MENON K: Patient name - …
#
# A line without a header continues the message above it. Files appear only in an
# export, as "upper.stl (file attached)" (Android) or "<attached: 0003-upper.stl>"
# (iPhone); a copy from WhatsApp Web carries none. WhatsApp sprinkles invisible
# direction marks and narrow no-break spaces through all of it.
INVISIBLE = dict.fromkeys(map(ord, '‎‏‪‫‬‭‮﻿'), None)
HEADER_BRACKET = re.compile(r'^\[(?P<stamp>[^\]]{6,40})\]\s*(?P<who>[^:\n]{1,60}?):\s?(?P<msg>.*)$')
HEADER_DASH = re.compile(
    r'^(?P<stamp>\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4},?\s+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?'
    r'\s*(?:[ap]\.?\s?m\.?)?)\s+-\s+(?P<who>[^:\n]{1,60}?):\s?(?P<msg>.*)$', re.I)
ATTACHED_ANDROID = re.compile(r'(?P<f>[^\n<>]+?\.[A-Za-z0-9]{2,5})\s*\(file attached\)', re.I)
ATTACHED_IOS = re.compile(r'<attached:\s*(?:\d+-)?(?P<f>[^>]+)>', re.I)
MEDIA_OMITTED = re.compile(r'<\s*media omitted\s*>', re.I)
PHONE_RX = re.compile(r'^\+?[\d\s()-]{8,}$')
MERGE_MINUTES = 15


def normalise_chat(text):
    return (text or '').translate(INVISIBLE).replace(' ', ' ').replace(' ', ' ')


def clean_poster(who):
    """'~ ANJALI MENON K' -> 'ANJALI MENON K'; a phone stays a phone."""
    return _clean((who or '').lstrip('~').strip())


def poster_phone(who):
    """The digits of a poster shown as a number (an unsaved contact), else ''."""
    who = clean_poster(who)
    return re.sub(r'\D', '', who) if PHONE_RX.match(who) else ''


def _stamp(value):
    try:
        return dateparser.parse(value.replace(',', ' '), dayfirst=True, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None


def _take_files(text):
    files = [m.group('f').strip() for m in ATTACHED_ANDROID.finditer(text)]
    files += [m.group('f').strip() for m in ATTACHED_IOS.finditer(text)]
    if MEDIA_OMITTED.search(text):
        files.append('(media)')
    text = MEDIA_OMITTED.sub('', ATTACHED_IOS.sub('', ATTACHED_ANDROID.sub('', text)))
    return text.strip(), files


def split_chat(text):
    """A pasted run of WhatsApp messages, one dict per message:
    {'when': datetime|None, 'poster': str, 'phone': str, 'text': str, 'files': [..]}.

    A message carrying only files, from the same person within a quarter of an hour
    of their previous one, is folded into it: clinics send the caption and then the
    STLs as separate messages, and they are one scan."""
    messages = []
    for raw in normalise_chat(text).splitlines():
        line = raw.rstrip()
        match = HEADER_BRACKET.match(line.strip()) or HEADER_DASH.match(line.strip())
        if match and _stamp(match.group('stamp')):
            who = clean_poster(match.group('who'))
            messages.append({'when': _stamp(match.group('stamp')), 'poster': who,
                             'phone': poster_phone(who), 'lines': [match.group('msg')]})
        elif messages:
            messages[-1]['lines'].append(line)
        elif line.strip():
            messages.append({'when': None, 'poster': '', 'phone': '', 'lines': [line]})
    out = []
    for message in messages:
        body, files = _take_files('\n'.join(message.pop('lines')))
        message.update(text=body, files=files)
        prev = out[-1] if out else None
        if (prev and not body and files and message['poster'] == prev['poster']
                and (not message['when'] or not prev['when']
                     or abs((message['when'] - prev['when']).total_seconds()) <= MERGE_MINUTES * 60)):
            prev['files'] += files
            continue
        out.append(message)
    return out


# ---------------------------------------------------------------------- arches
BOTH_RX = re.compile(r'\b(u\s*/\s*l|u\s*&\s*l|ul|both(\s+arch(es)?)?|upper\s*(and|&|\+)\s*lower)\b', re.I)
UPPER_RX = re.compile(r'\b(upper|maxill\w*)\b', re.I)
LOWER_RX = re.compile(r'\b(lower|mandib\w*)\b', re.I)


def guess_arch(text):
    """Which arches the doctor asked for, from how they wrote it: 'Upper & Lower
    Hawley', 'U/L Essix', 'upper bilateral expansion', 'lower retainer'. None
    when the words say nothing - a bite plate, a name."""
    text = text or ''
    if BOTH_RX.search(text):
        return 'both'
    upper, lower = bool(UPPER_RX.search(text)), bool(LOWER_RX.search(text))
    if upper and lower:
        return 'both'
    if upper:
        return 'upper'
    if lower:
        return 'lower'
    return None


def read_chat_message(message):
    """One split message, read the way a single caption is, plus who and when.
    `looks_like_scan` is false for chat ("do u have a 9mm screw??") - no patient,
    no delivery date and no file means nothing to register."""
    found = parse_whatsapp(message.get('text', ''))
    found.update(when=message.get('when'), poster=message.get('poster', ''),
                 phone=message.get('phone', ''), files=list(message.get('files') or []))
    found['looks_like_scan'] = bool(found.get('patient') or found.get('delivery') or found['files'])
    return found
