import os
import re
import csv
import io
import glob
import json
import shutil
import zipfile
import subprocess
import xml.etree.ElementTree as ET
import requests
from datetime import datetime

ARABIC_DIGITS_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def normalize_digits(text: str) -> str:
    if not text:
        return ""
    s = str(text).translate(ARABIC_DIGITS_MAP)
    # Remove Unicode directional isolates (e.g. \u2066, \u2069, \u200e, \u200f, \u202a-\u202e)
    s = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", s)
    return s

def normalize_arabic(text: str) -> str:
    if not text:
        return ""
    text = normalize_digits(text)
    text = re.sub(r"[أإآ]", "ا", text)
    text = re.sub(r"ة", "ه", text)
    text = re.sub(r"ى", "ي", text)
    return text

def collapse_spaced_phones(text: str) -> str:
    """Collapses spaces inside Egyptian mobile numbers like '012 00181577' or '+20 12 84231985'."""
    if not text:
        return ""
    s = normalize_digits(text)
    def _repl(m):
        return re.sub(r"[\s\-]+", "", m.group(0))
    return re.sub(r"(?:\+?20\s*|0020\s*|0\s*)?1\s*[0125](?:\s*\d){8}\b", _repl, s)

def clean_phone(p: str) -> str:
    if not p:
        return ""
    p = normalize_digits(str(p)).strip().strip('"').strip("'")
    # Handle Excel scientific notation e.g. "1.092246252E9"
    if re.match(r"^\d+\.\d+[eE]\+?\d+$", p):
        try:
            p = str(int(round(float(p))))
        except Exception:
            pass
    if p.endswith(".0"):
        p = p[:-2]
    p = re.sub(r"[^\d]", "", p)
    if p.startswith("00201") and len(p) == 14:
        p = "0" + p[4:]
    elif p.startswith("201") and len(p) == 12:
        p = "0" + p[2:]
    elif len(p) == 10 and p.startswith("1") and p[1] in "0125":
        # Google Sheets often strips the leading 0 (e.g. 1093552965 -> 01093552965)
        p = "0" + p
    return p

def clean_student_name_str(name: str) -> str:
    if not name:
        return ""
    name = str(name).strip()
    name = re.sub(r'^[\s"\'\:\-\*\/\\،,\.]+|[\s"\'\:\-\*\/\\،,\.]+$', '', name)
    return name.strip()

def format_time_24h(time_str: str) -> str:
    """Converts time strings like '01:12 PM', '1:45 م', or '2026-11-11 13:45' into 'HH:MM' 24-hour format."""
    if not time_str:
        return datetime.now().strftime("%H:%M")
    s = normalize_digits(str(time_str)).strip()
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM|ص|م)?", s, re.IGNORECASE)
    if not m:
        return datetime.now().strftime("%H:%M")
    hh = int(m.group(1))
    mm = int(m.group(2))
    ampm = (m.group(3) or "").upper()
    if ampm in ("PM", "م") and hh < 12:
        hh += 12
    elif ampm in ("AM", "ص") and hh == 12:
        hh = 0
    return f"{hh:02d}:{mm:02d}"

GRADE_PATTERNS = [
    (r"(?<!\d)(?:الصف\s*)?(الرابع|رابعه|رابعة|4)\s*(?:ال)?(ابتدائي|ابتدائى|ب)\b", "J4", "رابعة ابتدائي"),
    (r"(?<!\d)(?:الصف\s*)?(الخامس|خامسه|خامسة|5)\s*(?:ال)?(ابتدائي|ابتدائى|ب)\b", "J5", "خامسة ابتدائي"),
    (r"(?<!\d)(?:الصف\s*)?(السادس|ساته|سادسه|سادسة|سته|ستة|6)\s*(?:ال)?(ابتدائي|ابتدائى|ب)\b", "J6", "سادسة ابتدائي"),
    (r"الصف\s*(الرابع|رابعه)\b", "J4", "رابعة ابتدائي"),
    (r"الصف\s*(الخامس|خامسه)\b", "J5", "خامسة ابتدائي"),
    (r"الصف\s*(السادس|سادسه|سادسة)\b", "J6", "سادسة ابتدائي"),
    (r"(?<!\d)(?:الصف\s*)?(الاول|الأول|اولي|اولى|أولى|1)\s*(?:ال)?(اعدادي|اعدادى|إعدادي|ع)\b", "M1", "أولى إعدادي"),
    (r"(?<!\d)(?:الصف\s*)?(الثاني|الثانى|تانيه|تانية|ثانيه|ثانية|2)\s*(?:ال)?(اعدادي|اعدادى|إعدادي|ع)\b", "M2", "تانية إعدادي"),
    (r"(?<!\d)(?:الصف\s*)?(الثالث|تالته|تالتة|ثالثه|ثالثة|3)\s*(?:ال)?(اعدادي|اعدادى|إعدادي|ع)\b", "M3", "تالتة إعدادي"),
    (r"(?<!\d)(?:الصف\s*)?(الاول|الأول|اولي|اولى|أولى|1)\s*(?:ال)?(ثانوي|ثانوى|بكالوريا|بكلوريا|ث)\b", "S1", "أولى ثانوي (بكالوريا)"),
    (r"(?<!\d)(?:الصف\s*)?(الثاني|الثانى|تانيه|تانية|ثانيه|ثانية|2)\s*(?:ال)?(بكالوريا|بكلوريا)\b", "BC", "تانية بكالوريا"),
    (r"(?<!\d)(?:الصف\s*)?(الثالث|تالته|تالتة|ثالثه|ثالثة|3)\s*(?:ال)?(بكالوريا|بكلوريا)\b", "BC", "تالتة بكالوريا"),
    (r"(?<!\d)(?:الصف\s*)?(الثاني|الثانى|تانيه|تانية|ثانيه|ثانية|2)\s*(?:ال)?(ثانوي|ثانوى|ث)\b", "S2", "تانية ثانوي"),
    (r"(?<!\d)(?:الصف\s*)?(الثالث|تالته|تالتة|ثالثه|ثالثة|3)\s*(?:ال)?(ثانوي|ثانوى|ث)\b", "S3", "تالتة ثانوي"),
    (r"\b(بكالوريا|بكلوريا|baccalaureate|bac)\b", "BC", "بكالوريا"),
]

SUBJECT_PATTERNS = [
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(انجليزي|انجلش|اللغه الانجليزيه|لغه انجليزيه|english|eng)(?:$|[\s،,/+\-\(\)\\:])", "ENGLISH", "إنجليزي"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(ماث|ماس|math|maths)(?:$|[\s،,/+\-\(\)\\:])", "MATH", "ماث (Math)"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(رياضيات|رياضه|رياضة)(?:$|[\s،,/+\-\(\)\\:])", "MATH_AR", "رياضيات"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(ساينس|سينس|science)(?:$|[\s،,/+\-\(\)\\:])", "SCIENCE", "ساينس (Science)"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(علوم\s*متكامله|علوم\s*متكاملة|integrated\s*sciences)(?:$|[\s،,/+\-\(\)\\:])", "INTEGRATED_SCIENCES", "علوم متكاملة"),
    (r"(?<!متكامله\s)(?<!متكاملة\s)(?:^|[\s،,/+\-\(\)\\:]|و)(علوم)(?:$|[\s،,/+\-\(\)\\:])", "SCIENCE_AR", "علوم"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(عربي|عربى|اللغه العربيه|لغه عربيه|لغة عربية|ماده العربي|مادة العربي|arabic)(?:$|[\s،,/+\-\(\)\\:])", "ARABIC", "عربي"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(دراسات|social\s*studies)(?:$|[\s،,/+\-\(\)\\:])", "SOCIAL_STUDIES", "دراسات اجتماعية"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(فيزياء|فيزيكس|physics)(?:$|[\s،,/+\-\(\)\\:])", "PHYSICS", "فيزياء"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(كيمياء|كيمستري|chemistry)(?:$|[\s،,/+\-\(\)\\:])", "CHEMISTRY", "كيمياء"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(احياء|بيولوجي|biology)(?:$|[\s،,/+\-\(\)\\:])", "BIOLOGY", "أحياء"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(تاريخ|history)(?:$|[\s،,/+\-\(\)\\:])", "HISTORY", "تاريخ"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(جغرافيا|geography)(?:$|[\s،,/+\-\(\)\\:])", "GEOGRAPHY", "جغرافيا"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(فلسفه|فلسفة|philosophy)(?:$|[\s،,/+\-\(\)\\:])", "PHILOSOPHY", "فلسفة"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(علم\s*النفس|علم\s*نفس|psychology)(?:$|[\s،,/+\-\(\)\\:])", "PSYCHOLOGY", "علم النفس"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(فرنساوي|فرنساوى|فرنسي|french)(?:$|[\s،,/+\-\(\)\\:])", "FRENCH", "فرنساوي"),
    (r"(?:^|[\s،,/+\-\(\)\\:]|و)(الماني|ألماني|المانى|deutsch|german)(?:$|[\s،,/+\-\(\)\\:])", "DEUTSCH", "ألماني"),
]

SYSTEM_SUBJECT_BUTTON_MAP = {
    "ENGLISH": ["ENGLISH", "English"],
    "MATH": ["MATH", "Math", "MATHEMATICS"],
    "MATH_AR": ["MATH", "Math", "رياضيات", "MATHEMATICS"],
    "SCIENCE": ["SCIENCE", "Science"],
    "SCIENCE_AR": ["SCIENCE", "Science", "علوم"],
    "INTEGRATED_SCIENCES": ["INTEGRATED_SCIENCES", "INTEGRATED SCIENCE", "SCIENCE", "علوم متكاملة"],
    "ARABIC": ["ARABIC", "Arabic", "عربي"],
    "SOCIAL_STUDIES": ["SOCIAL", "SOCIAL_STUDIES", "Social Studies", "دراسات"],
    "PHYSICS": ["PHYSICS", "Physics", "فيزياء"],
    "CHEMISTRY": ["CHEMISTRY", "Chemistry", "كيمياء"],
    "BIOLOGY": ["BIOLOGY", "Biology", "أحياء"],
    "HISTORY": ["HISTORY", "History", "تاريخ"],
    "GEOGRAPHY": ["GEOGRAPHY", "Geography", "جغرافيا"],
    "PHILOSOPHY": ["PHILOSOPHY", "Philosophy", "فلسفة"],
    "PSYCHOLOGY": ["PSYCHOLOGY", "Psychology", "علم النفس"],
    "FRENCH": ["FRENCH", "French", "فرنساوي"],
    "DEUTSCH": ["DEUTSCH", "GERMAN", "German", "ألماني"],
}

def extract_drive_file_id(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(?:id=|/file/d/|/d/)([a-zA-Z0-9_-]{20,})", str(text))
    return m.group(1) if m else ""


def extract_spreadsheet_id_and_gid(sheet_url_or_id: str) -> tuple:
    if not sheet_url_or_id:
        return ("", "0")
    s = sheet_url_or_id.strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]{15,})", s)
    sheet_id = m.group(1) if m else ""
    if not sheet_id and re.match(r"^[a-zA-Z0-9_-]{15,}$", s):
        sheet_id = s
    gid_m = re.search(r"[#?&]gid=(\d+)", s)
    gid = gid_m.group(1) if gid_m else "0"
    return (sheet_id, gid)


def _find_gdrive_cmd() -> list:
    """Finds the gdrive CLI command candidates on Windows."""
    candidates = []
    which_gd = shutil.which("gdrive")
    if which_gd:
        candidates.append([which_gd])
    # Check temp agy-plugin-cli directories
    temp_dir = os.environ.get("TEMP", r"C:\Users\ROOT\AppData\Local\Temp")
    for cmd_path in sorted(glob.glob(os.path.join(temp_dir, "agy-plugin-cli-*", "gdrive.cmd")), reverse=True):
        if os.path.isfile(cmd_path):
            candidates.append([cmd_path])
            break
    lang_srv = r"C:\Users\ROOT\AppData\Local\Programs\antigravity\resources\bin\language_server.exe"
    if os.path.isfile(lang_srv):
        candidates.append([lang_srv, "plugin-cli", "gdrive"])
    return candidates


def _read_xlsx_rows(xlsx_path: str, sheet_index: int = 0) -> list:
    """Pure-Python reader for .xlsx files using built-in zipfile + xml.etree.ElementTree (strictly read-only)."""
    rows_data = []
    with zipfile.ZipFile(xlsx_path, "r") as z:
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            ss_xml = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in ss_xml.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                texts = [t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
                shared_strings.append("".join(texts))

        sheet_files = sorted([n for n in z.namelist() if re.match(r"^xl/worksheets/sheet\d+\.xml$", n)])
        if not sheet_files:
            return []
        target_sheet = sheet_files[min(sheet_index, len(sheet_files) - 1)]
        ws_xml = ET.fromstring(z.read(target_sheet))

        def col_letters_to_idx(col_str: str) -> int:
            idx = 0
            for ch in col_str:
                if "A" <= ch <= "Z":
                    idx = idx * 26 + (ord(ch) - ord("A") + 1)
            return idx - 1

        max_row_num = 0
        row_map = {}
        for row_el in ws_xml.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            r_num = int(row_el.attrib.get("r", len(row_map) + 1))
            max_row_num = max(max_row_num, r_num)
            cells = {}
            max_col = 0
            for c_el in row_el.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                ref = c_el.attrib.get("r", "A1")
                col_part = re.sub(r"\d+", "", ref)
                c_idx = col_letters_to_idx(col_part)
                max_col = max(max_col, c_idx)
                c_type = c_el.attrib.get("t", "")
                v_el = c_el.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                is_el = c_el.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is")
                val = ""
                if c_type == "s" and v_el is not None and v_el.text is not None:
                    s_idx = int(v_el.text)
                    val = shared_strings[s_idx] if 0 <= s_idx < len(shared_strings) else ""
                elif c_type == "inlineStr" and is_el is not None:
                    val = "".join([t.text or "" for t in is_el.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")])
                elif v_el is not None and v_el.text is not None:
                    val = v_el.text
                cells[c_idx] = val
            row_list = [cells.get(i, "") for i in range(max_col + 1)]
            row_map[r_num] = row_list

        for r_i in range(1, max_row_num + 1):
            rows_data.append(row_map.get(r_i, []))
    return rows_data


def fetch_google_sheet_row(sheet_url_or_id: str, row_number: int, upload_dir: str) -> dict:
    """
    Strictly READ-ONLY fetcher for a specific Row Number (1-indexed, matching Google Sheets UI)
    from a Google Sheet. Never modifies or writes to the Google Sheet.
    """
    sheet_id, gid = extract_spreadsheet_id_and_gid(sheet_url_or_id)
    if not sheet_id:
        raise ValueError("لينك الشيت غير صالح. يرجى وضع لينك Google Sheet صحيح.")
    if row_number < 1:
        raise ValueError("رقم الـ Row يجب أن يكون 1 أو أكبر.")

    os.makedirs(upload_dir, exist_ok=True)
    rows = []

    # 1. Authenticated gdrive readonly export (.xlsx) — handles private/shared Google Sheets cleanly
    import time as _time
    temp_xlsx = os.path.join(upload_dir, f"sheet_{sheet_id}.xlsx")
    is_recent = os.path.isfile(temp_xlsx) and os.path.getsize(temp_xlsx) > 100 and (_time.time() - os.path.getmtime(temp_xlsx) < 25)
    if is_recent:
        try:
            rows = _read_xlsx_rows(temp_xlsx, 0)
        except Exception:
            rows = []

    if not rows:
        for gd_prefix in _find_gdrive_cmd():
            try:
                if os.path.isfile(temp_xlsx):
                    try:
                        os.remove(temp_xlsx)
                    except OSError:
                        pass
                proc = subprocess.run(
                    gd_prefix + ["readonly", "export", sheet_id, temp_xlsx, "--format", "xlsx"],
                    capture_output=True, text=True, timeout=30
                )
                if proc.returncode == 0 and os.path.isfile(temp_xlsx) and os.path.getsize(temp_xlsx) > 100:
                    rows = _read_xlsx_rows(temp_xlsx, 0)
                    if rows:
                        break
            except Exception:
                pass

    # If cached xlsx exists and is recent (e.g. if subprocess env wasn't attached), use it
    if not rows and os.path.isfile(temp_xlsx) and os.path.getsize(temp_xlsx) > 100:
        try:
            rows = _read_xlsx_rows(temp_xlsx, 0)
        except Exception:
            pass

    # 2. Fallback: Direct XLSX / CSV export URL (works on any employee PC with network access)
    if not rows:
        try:
            xlsx_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
            r_x = requests.get(xlsx_url, timeout=20)
            if r_x.status_code == 200 and len(r_x.content) > 500 and r_x.content[:2] == b"PK":
                with open(temp_xlsx, "wb") as f:
                    f.write(r_x.content)
                rows = _read_xlsx_rows(temp_xlsx, 0)
        except Exception:
            pass

    if not rows:
        export_urls = [
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}",
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}",
        ]
        for url in export_urls:
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 200 and len(resp.content) > 10 and b"<!doctype html" not in resp.content[:300].lower() and b"<html" not in resp.content[:300].lower():
                    csv_text = resp.content.decode("utf-8-sig", errors="replace")
                    rows = list(csv.reader(io.StringIO(csv_text)))
                    if rows:
                        break
            except Exception:
                pass

    if not rows:
        raise RuntimeError(
            "تعذر قراءة الشيت. تأكد من أن اللينك صحيح وأن الحساب مسموح له بعرض الشيت (Viewer)."
        )

    if row_number > len(rows):
        raise ValueError(
            f"رقم الـ Row ({row_number}) غير موجود في الشيت حالياً (إجمالي الصفوف في الشيت: {len(rows)} صف)."
        )

    target_row = [str(c).strip() for c in rows[row_number - 1]]
    if not any(target_row):
        raise ValueError(f"الـ Row رقم ({row_number}) فارغ في الشيت!")

    return build_sheet_row_dict(row_number, target_row, rows[0] if rows else [])


def build_sheet_row_dict(row_number: int, target_row: list, header_row: list = None) -> dict:
    """Builds the normalized sheet_row dictionary from raw row cells (from xlsx/csv or browser JSONP)."""
    target_row = [str(c).strip() if c is not None else "" for c in target_row]
    student_col = 3   # Default Column D: بيانات الطالب
    drive_col = 4     # Default Column E: اسكرين التحويل بتاعت الطالب
    wallet_col = 5    # Default Column F: Wallet Number
    transfer_col = 6  # Default Column G: الرقم المحول منه

    if header_row:
        h_row = [normalize_arabic(str(c)).strip().lower() for c in header_row]
        for idx, h_txt in enumerate(h_row):
            if "بيانات الطالب" in h_txt:
                student_col = idx
            elif "اسكرين" in h_txt or "تحويل بتاعت" in h_txt:
                drive_col = idx
            elif "wallet" in h_txt or "محفظه المستلمه" in h_txt:
                wallet_col = idx
            elif "الرقم المحول منه" in h_txt or "محول منه" in h_txt:
                transfer_col = idx

    def safe_cell(idx: int) -> str:
        return target_row[idx].strip() if 0 <= idx < len(target_row) else ""

    student_block = safe_cell(student_col)
    drive_url = safe_cell(drive_col)
    drive_file_id = extract_drive_file_id(drive_url)
    sheet_wallet_raw = safe_cell(wallet_col)
    sheet_transfer_number = clean_phone(safe_cell(transfer_col))
    if not re.match(r"^01[0125]\d{8}$", sheet_transfer_number):
        sheet_transfer_number = ""

    for idx, cell in enumerate(target_row):
        if not cell:
            continue
        fid = extract_drive_file_id(cell)
        if fid and not drive_file_id:
            drive_url = cell
            drive_file_id = fid
            continue
        if ("#" in cell and re.search(r"0?1[0125]\d{8}", normalize_digits(cell))) and not sheet_wallet_raw:
            sheet_wallet_raw = cell
            continue

    sheet_wallet_phone = ""
    sheet_wallet_tag = ""
    if sheet_wallet_raw:
        ph_m = re.search(r"(0?1[0125]\d{8})", normalize_digits(sheet_wallet_raw))
        if ph_m:
            sheet_wallet_phone = clean_phone(ph_m.group(1))
        tag_m = re.search(r"(#\w+)", sheet_wallet_raw)
        if tag_m:
            sheet_wallet_tag = tag_m.group(1)

    quoted_block = '"' + student_block.replace('"', ' ') + '"'
    combined_tsv = "\t".join([
        quoted_block,
        drive_url,
        sheet_wallet_raw,
        sheet_transfer_number
    ])

    return {
        "row_number": row_number,
        "raw_cells": target_row,
        "combined_tsv": combined_tsv,
        "student_block": student_block,
        "drive_url": drive_url,
        "drive_file_id": drive_file_id,
        "sheet_wallet_raw": sheet_wallet_raw,
        "sheet_wallet_phone": sheet_wallet_phone,
        "sheet_wallet_tag": sheet_wallet_tag,
        "sheet_transfer_number": sheet_transfer_number,
        "agent_name": safe_cell(2),
        "coordinator_name": safe_cell(1),
        "subscription_record_id": safe_cell(7),
    }


def extract_sheet_columns(raw_text: str) -> dict:
    if not raw_text:
        return {
            "student_block": "",
            "drive_url": "",
            "drive_file_id": "",
            "sheet_wallet_raw": "",
            "sheet_wallet_phone": "",
            "sheet_wallet_tag": "",
            "sheet_transfer_number": "",
        }

    text = raw_text.strip()
    drive_url = ""
    drive_file_id = ""
    sheet_wallet_raw = ""
    sheet_wallet_phone = ""
    sheet_wallet_tag = ""
    sheet_transfer_number = ""
    student_block = text

    if "\t" in text:
        try:
            reader = csv.reader(io.StringIO(text), delimiter="\t", quotechar='"')
            rows = [r for r in reader if any(cell.strip() for cell in r)]
            if rows:
                first_row = rows[0]
                student_block = first_row[0].strip() if len(first_row) >= 1 else text
                for cell in first_row[1:]:
                    c = cell.strip()
                    if not c:
                        continue
                    fid = extract_drive_file_id(c)
                    if fid and not drive_file_id:
                        drive_url = c
                        drive_file_id = fid
                        continue
                    if "#" in c or re.match(r"^0?1[0125]\d{8}\s*[-#]", normalize_digits(c)):
                        sheet_wallet_raw = c
                        ph_m = re.search(r"(0?1[0125]\d{8})", normalize_digits(c))
                        if ph_m:
                            sheet_wallet_phone = clean_phone(ph_m.group(1))
                        tag_m = re.search(r"(#\w+)", c)
                        if tag_m:
                            sheet_wallet_tag = tag_m.group(1)
                        continue
                    ph_only = clean_phone(c)
                    if re.match(r"^01[0125]\d{8}$", ph_only):
                        if not sheet_transfer_number:
                            sheet_transfer_number = ph_only
                        elif not sheet_wallet_phone:
                            sheet_wallet_phone = ph_only
        except Exception:
            pass

    if not drive_file_id:
        url_m = re.search(r"(https?://drive\.google\.com/\S+)", text)
        if url_m:
            drive_url = url_m.group(1)
            drive_file_id = extract_drive_file_id(drive_url)
            idx = text.find(drive_url)
            student_block = text[:idx].strip().strip('"')
            tail = text[idx + len(drive_url):].strip()
            if tail:
                w_m = re.search(r"(0?1[0125]\d{8})\s*-\s*(#\w+)", normalize_digits(tail))
                if w_m:
                    sheet_wallet_raw = w_m.group(0)
                    sheet_wallet_phone = clean_phone(w_m.group(1))
                    sheet_wallet_tag = w_m.group(2)
                    tail = tail.replace(w_m.group(0), "")
                rem_phones = [clean_phone(p) for p in re.findall(r"\b0?1[0125]\d{8}\b", normalize_digits(tail))]
                if rem_phones:
                    sheet_transfer_number = rem_phones[-1]

    student_block = student_block.strip().strip('"').strip()

    return {
        "student_block": student_block,
        "drive_url": drive_url,
        "drive_file_id": drive_file_id,
        "sheet_wallet_raw": sheet_wallet_raw,
        "sheet_wallet_phone": sheet_wallet_phone,
        "sheet_wallet_tag": sheet_wallet_tag,
        "sheet_transfer_number": sheet_transfer_number,
    }


def download_drive_image(drive_file_id: str, upload_dir: str) -> str:
    """
    Downloads a screenshot image from Google Drive by File ID (strictly read-only).
    """
    if not drive_file_id:
        return ""
    os.makedirs(upload_dir, exist_ok=True)
    dest_path = os.path.join(upload_dir, f"drive_{drive_file_id}.jpg")
    if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1024:
        return dest_path

    for gd_prefix in _find_gdrive_cmd():
        try:
            proc = subprocess.run(
                gd_prefix + ["readonly", "download", drive_file_id, "--out", dest_path],
                capture_output=True, text=True, timeout=25
            )
            if proc.returncode == 0 and os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1024:
                return dest_path
        except Exception:
            pass

    for url in [
        f"https://drive.google.com/uc?export=download&id={drive_file_id}",
        f"https://drive.google.com/thumbnail?id={drive_file_id}&sz=w1600",
        f"https://lh3.googleusercontent.com/d/{drive_file_id}=w1600",
    ]:
        try:
            resp = requests.get(url, timeout=20)
            if resp.status_code == 200 and len(resp.content) > 1024 and b"<html" not in resp.content[:300].lower():
                with open(dest_path, "wb") as f:
                    f.write(resp.content)
                return dest_path
        except Exception:
            pass

    return ""


def parse_student_text(raw_text: str) -> dict:
    """
    Pure deterministic parser for student registration messages AND Google Sheet rows.
    """
    if not raw_text:
        return {}

    sheet_cols = extract_sheet_columns(raw_text)
    clean_block = sheet_cols["student_block"] or raw_text.strip()

    # Collapse spaces inside phone numbers (e.g. '012 00181577' -> '01200181577')
    text = collapse_spaced_phones(normalize_digits(clean_block.strip()))
    norm_text = normalize_arabic(text)
    lines = [line.strip().strip('"').strip("\\").strip() for line in text.splitlines() if line.strip().strip('"').strip("\\").strip()]

    student_name = ""
    student_phone = ""
    parent_phone = ""
    transfer_phone = sheet_cols.get("sheet_transfer_number", "")
    academic_year = ""

    # 1. Extract Student Name
    for idx, line in enumerate(lines):
        norm_line = normalize_arabic(line)
        # Skip phone lines like 'رقم الطالب' or 'رقم الطالبة'
        if re.search(r"رقم\s*(?:الطالبه|الطالبة|الطالب|ولي|ولى|التلفون|التليفون|الموبايل|التحويل)", norm_line):
            continue
        # Skip narrative notes starting with 'الطالب كان...' or 'الطالب دفع...'
        if re.search(r"^(?:الطالبه|الطالبة|الطالب)\s+(?:كان|كانت|مشترك|مشتركه|دفع|دفعت|حول|حولت|اشترك|اشتركت|عايز|عايزه)", norm_line):
            continue
        m = re.search(r"^(?:اسم\s*الطالبة|اسم\s*الطالبه|اسم\s*الطالب|الطالبة|الطالبه|الطالب|الاسم)\s*[:/\-–]?\s*(.*)", norm_line)
        if m:
            candidate = m.group(1).strip()
            if candidate and not re.search(r"\d{7,}", candidate):
                orig_m = re.search(r"^(?:اسم\s*الطالبة|اسم\s*الطالبه|اسم\s*الطالب|الطالبة|الطالبه|الطالب|الاسم)\s*[:/\-–]?\s*(.*)", line, re.IGNORECASE)
                student_name = clean_student_name_str(orig_m.group(1) if orig_m else candidate)
                break
            elif not candidate and idx + 1 < len(lines):
                # Name is on the next line (e.g. "اسم الطالب \n :سلمى احمد حمدي")
                student_name = clean_student_name_str(lines[idx + 1])
                break

    if not student_name and lines:
        # Check first line: if it has a name + phone (e.g. "محمد سيد محمد 01111130270"), strip the phone
        first_line = re.sub(r"(?:00201|\+?201|0?1)[0125]\d{8}", "", lines[0]).strip()
        norm_first = normalize_arabic(first_line)
        if first_line and not any(k in norm_first for k in ["ابتدائي", "اعدادي", "ثانوي", "بكالوريا", "بكلوريا", "خطه", "كلاس", "محتوي", "شهر", "ترم", "سنه"]):
            student_name = clean_student_name_str(re.sub(r"^.*?[:/\-]\s*", "", first_line))
        else:
            # Scan other lines for a pure name line
            for line in lines[1:]:
                cand = re.sub(r"(?:00201|\+?201|0?1)[0125]\d{8}", "", line).strip()
                norm_c = normalize_arabic(cand)
                if cand and len(cand.split()) >= 2 and not re.search(r"\d", cand):
                    if not any(k in norm_c for k in ["ابتدائي", "اعدادي", "ثانوي", "بكالوريا", "بكلوريا", "خطه", "كلاس", "محتوي", "شهر", "ترم", "سنه", "المواد", "التخصص", "باقه", "ولي الامر", "طالب"]):
                        student_name = clean_student_name_str(cand)
                        break

    # 2. Extract Student Phone, Parent Phone, Transfer Phone
    for line in lines:
        norm_line = normalize_arabic(line)
        phones_in_line = re.findall(r"(?:00201|\+?201|0?1)[0125]\d{8}", line)
        cleaned_phones = [clean_phone(p) for p in phones_in_line if clean_phone(p)]
        if not cleaned_phones:
            continue
        has_student_kw = any(k in norm_line for k in ["طالب", "طالبه"])
        has_parent_kw = any(k in norm_line for k in ["ولي الامر", "ولي أمر", "وليه الامر", "وبلي الامر", "اب", "ام", "parent"])
        has_transfer_kw = any(k in norm_line for k in ["تحويل", "حولت منه", "هحول منو", "حول منه"])

        if has_student_kw and has_parent_kw:
            if not student_phone:
                student_phone = cleaned_phones[0]
            if not parent_phone:
                parent_phone = cleaned_phones[-1]
        elif has_transfer_kw:
            if not transfer_phone:
                transfer_phone = cleaned_phones[0]
        elif has_parent_kw:
            if not parent_phone:
                parent_phone = cleaned_phones[0]
        elif has_student_kw or any(k in norm_line for k in ["تلفون", "تليفون", "موبايل", "هاتف", "رقم"]):
            if not student_phone:
                student_phone = cleaned_phones[0]
            elif not parent_phone and cleaned_phones[0] != student_phone:
                parent_phone = cleaned_phones[0]

    all_phones = []
    for p in re.findall(r"\b(?:00201|\+?201|0?1)[0125]\d{8}\b", text):
        cp = clean_phone(p)
        if cp and cp not in all_phones:
            all_phones.append(cp)

    if not student_phone and len(all_phones) >= 1:
        student_phone = all_phones[0]
    if not parent_phone:
        if len(all_phones) >= 2:
            for p in all_phones[1:]:
                if p != student_phone:
                    parent_phone = p
                    break
        elif student_phone:
            parent_phone = student_phone
    if not transfer_phone and len(all_phones) >= 3:
        transfer_phone = all_phones[2]

    # 3. Extract Academic Year
    year_m = re.search(r"(202\d\s*[/\-]\s*202\d)", text)
    if year_m:
        academic_year = year_m.group(1).replace(" ", "")

    # 4. Education Type (لغات / عام / مشترك)
    norm_track_text = re.sub(r"\(?\s*(?:عربي|عام)\s*(?:ولا|او|أو|/|\-)\s*لغات\s*\)?", " ", norm_text)
    education_type = "عام"
    edu_code = "AR"
    if re.search(r"(لغات|تجريبي|انترناشونال|languages|lang)", norm_track_text, re.IGNORECASE):
        education_type = "لغات"
        edu_code = "EN"
    elif re.search(r"(مشترك|shared)", norm_track_text, re.IGNORECASE):
        education_type = "مشترك"
        edu_code = "SHARED"
    elif not re.search(r"\bعام\b", norm_track_text) and re.search(r"\b(ماث|ساينس|math|science|physics|chemistry|biology)\b", norm_track_text, re.IGNORECASE):
        education_type = "لغات"
        edu_code = "EN"

    # 5. Grade / Stage
    grade_code = ""
    grade_label = ""
    for pat, code, label in GRADE_PATTERNS:
        if re.search(pat, norm_text, re.IGNORECASE):
            grade_code = code
            grade_label = label
            break

    section_code = f"{grade_code}-{edu_code}" if grade_code else ""
    if grade_code == "BC":
        section_code = f"S2-{edu_code}-Baccalaureate"
    elif grade_code == "S2":
        if re.search(r"(ادبي|أدبي|art)", norm_text, re.IGNORECASE):
            section_code = f"S2-{edu_code}-ART"
        else:
            section_code = f"S2-{edu_code}-SCIENTIFIC"
    elif grade_code == "S3":
        if re.search(r"(ادبي|أدبي|art)", norm_text, re.IGNORECASE):
            section_code = f"S3-{edu_code}-ART"
        elif re.search(r"(رياضه|رياضة|رياضيات|ماث|math)", norm_text, re.IGNORECASE):
            section_code = f"S3-{edu_code}-MATH"
        else:
            section_code = f"S3-{edu_code}-SCIENCE"

    # 6. Duration (شهر / ترم / سنة)
    norm_for_dur = re.sub(r"السنه\s*الدراسيه\s*(?:202\d\s*[/\-]\s*202\d)?", "", norm_text)
    duration = "شهر"
    duration_code = "Month"
    if re.search(r"(?:^|[\s،,/+\-\(\)\.])(ترم|تيرم|term)(?:$|[\s،,/+\-\(\)\.])", norm_for_dur, re.IGNORECASE):
        duration = "ترم"
        duration_code = "Term"
    elif re.search(r"(?:^|[\s،,/+\-\(\)\.])(سنه|سنة|سنوي|سنوى|طول\s*السنه|باقه\s*السنه|في\s*السنه|سنه\s*كامله|عام\s*كامل|annual|full\s*year|year)(?:$|[\s،,/+\-\(\)\.])", norm_for_dur, re.IGNORECASE):
        duration = "سنة"
        duration_code = "Year"
    elif re.search(r"(?:^|[\s،,/+\-\(\)\.])(شهر|شهري|في\s*الشهر|monthly|month)(?:$|[\s،,/+\-\(\)\.])", norm_for_dur, re.IGNORECASE):
        duration = "شهر"
        duration_code = "Month"

    # 7. Package Tier (Content vs Class)
    package_tier = "Content"
    package_tier_ar = "محتوى (Content)"
    if re.search(r"(متابعه|متابعة|كلاس|خطه\s*كلاس|خطة\s*كلاس|لايف|follow\s*up|class)", norm_text, re.IGNORECASE):
        package_tier = "Class"
        package_tier_ar = "خطة كلاس (Class)"

    # 8. Sibling Discount flag
    is_sibling = bool(re.search(r"(اخوات|إخوات|اخين|اختين|sibling)", norm_text, re.IGNORECASE))

    # 9. Subjects or Full Bundle
    subjects = []
    subjects_ar = []
    is_full_bundle = bool(re.search(
        r"(باقه\s*كامله|الباقه\s*الكامله|كل\s*المواد|جميع\s*المواد|باندل|بندل|بادل|باكدج|باكج|all\s*subjects|bundle)",
        norm_text,
        re.IGNORECASE
    ))

    # Distinguish 'عربي' meaning Education Track ('عام') from 'عربي' meaning Arabic Subject ('ARABIC'):
    norm_for_subjects = norm_text
    # (a) Strip form template prompts like '(عربي ولا لغات)' or 'التخصص: عربي'
    norm_for_subjects = re.sub(r"\(?\s*عربي\s*(?:ولا|او|أو|/|\-)\s*لغات\s*\)?", " ", norm_for_subjects)
    norm_for_subjects = re.sub(
        r"التخصص\s*[:/\-]?\s*(?:عربي|عام|لغات)(?!\s*(?:و|\+|،|دراسات|علوم|رياض|انجل|ماث|ساينس))",
        " ",
        norm_for_subjects
    )
    # (b) Strip standalone line 'عربي' or 'عربي عام' / 'عام عربي'
    norm_for_subjects = re.sub(
        r"(?m)^\s*(?:التخصص\s*[:/\-]?\s*)?(?:عربي(?:\s+عام)?|عام\s+عربي)\s*$",
        " ",
        norm_for_subjects
    )
    # (c) Strip 'عربي' when it immediately follows the grade/stage on the same line (e.g. '3 اعدادي عربي')
    norm_for_subjects = re.sub(
        r"((?:ابتدائي|ابتدائى|اعدادي|اعدادى|إعدادي|ثانوي|ثانوى|بكالوريا|بكلوريا|الرابع|رابعه|الخامس|خامسه|السادس|سادسه|الاول|اولي|الثاني|تانيه|الثالث|تالته))\s*[\.\-/]?\s*عربي(?!\s*(?:و|\+|،|دراسات|علوم|رياض|انجل|ماث|ساينس|فقط))",
        r"\1 ",
        norm_for_subjects
    )
    # (d) If the message never explicitly mentioned 'عام' or 'لغات' anywhere, and 'عربي' is not qualified
    #     as a subject ('مادة عربي', 'لغة عربية', 'المواد: عربي', 'عربي فقط') nor joined with other subjects,
    #     then 'عربي' is specifying the Education Track ('عام'), not a single subject.
    has_explicit_track = bool(re.search(r"\b(عام|لغات|تجريبي|انترناشونال|languages|lang)\b", norm_track_text, re.IGNORECASE))
    has_explicit_arabic_subj = bool(re.search(
        r"(?:ماده|مادة|لغه|لغة|المواد)\s*[:/\-]?\s*(?:ال)?(?:لغه\s*)?(?:ال)?عربيه?|عربي\s*فقط|عربي\s*(?:و|\+|،)\s*\S+|\S+\s*(?:و|\+|،)\s*عربي",
        norm_for_subjects
    ))
    if not has_explicit_track and not has_explicit_arabic_subj:
        norm_for_subjects = re.sub(r"\bعربي\b", " ", norm_for_subjects)

    if not is_full_bundle:
        for pat, sub_code, sub_ar in SUBJECT_PATTERNS:
            if re.search(pat, norm_for_subjects, re.IGNORECASE):
                if sub_code not in subjects:
                    subjects.append(sub_code)
                    subjects_ar.append(sub_ar)

    if len(subjects) == 0:
        is_full_bundle = True

    return {
        "student_name": student_name,
        "student_phone": student_phone,
        "parent_phone": parent_phone,
        "transfer_phone": transfer_phone,
        "drive_url": sheet_cols.get("drive_url", ""),
        "drive_file_id": sheet_cols.get("drive_file_id", ""),
        "sheet_wallet_raw": sheet_cols.get("sheet_wallet_raw", ""),
        "sheet_wallet_phone": sheet_cols.get("sheet_wallet_phone", ""),
        "sheet_wallet_tag": sheet_cols.get("sheet_wallet_tag", ""),
        "academic_year": academic_year or "2026/2027",
        "education_type": education_type,
        "edu_code": edu_code,
        "grade_code": grade_code,
        "grade_label": grade_label,
        "section_code": section_code,
        "duration": duration,
        "duration_code": duration_code,
        "package_tier": package_tier,
        "package_tier_ar": package_tier_ar,
        "is_sibling": is_sibling,
        "is_full_bundle": is_full_bundle,
        "subjects": subjects,
        "subjects_ar": subjects_ar,
        "subjects_count": len(subjects),
    }


def run_windows_ocr(image_path: str) -> dict:
    """Runs native Windows.Media.Ocr via ocr_engine.ps1 and returns extracted text."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_engine.ps1")
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", script_path,
        "-ImagePath", os.path.abspath(image_path)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=25)
    if proc.returncode != 0:
        raise RuntimeError(f"OCR Failed: {proc.stderr}")
    raw_out = proc.stdout.strip()
    return json.loads(raw_out)


def parse_receipt_image(image_path: str) -> dict:
    """
    Pure deterministic parser for transfer screenshots (InstaPay, Vodafone Cash, Orange, Etisalat, Fawry).
    """
    ocr_data = run_windows_ocr(image_path)
    en_lines = ocr_data.get("en_lines", [])
    ar_lines = ocr_data.get("ar_lines", [])
    en_text = normalize_digits(ocr_data.get("en_text", ""))
    ar_text = normalize_digits(ocr_data.get("ar_text", ""))
    combined_text = en_text + "\n" + ar_text

    # 1. Detect Provider
    provider = "Wallet / Mobile Cash"
    if re.search(r"instapay|ipn|انستاباي|إنستاباي", combined_text, re.IGNORECASE):
        provider = "InstaPay"
    elif re.search(r"vodafone|vf[\-\s]*cash|فودافون|انا فودافون", combined_text, re.IGNORECASE):
        provider = "Vodafone Cash"
    elif re.search(r"etisalat|e&|اتصالات", combined_text, re.IGNORECASE):
        provider = "Etisalat Cash"
    elif re.search(r"orange|اورنچ|اورنج", combined_text, re.IGNORECASE):
        provider = "Orange Cash"
    elif re.search(r"fawry|فوري", combined_text, re.IGNORECASE):
        provider = "Fawry"

    # 2. Extract Paid Amount (EGP)
    paid_amount = None
    amt_patterns = [
        r"(?:جنيه|جنية|جنه|حنه|حنيه|EGP|LE)\s*(\d{2,5}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:تحويل|حول)",
        r"(?:تم\s*تحويل|تحويل\s*مبلغ|تم\s*استلام|مبلغ\s*التحويل)\s*[:\-]?\s*(\d{2,5}(?:,\d{3})*(?:\.\d{1,2})?)",
        r"\b(\d{2,5}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:EGP|LE|جنيه|جنية|جنه|حنه|حنيه|ج\.م)\b",
        r"(?:EGP|LE|جنيه|جنية|جنه|حنه|حنيه|ج\.م)\s*[:\-]?\s*(\d{2,5}(?:,\d{3})*(?:\.\d{1,2})?)\b",
    ]
    candidates = []
    for pat in amt_patterns:
        for m in re.finditer(pat, combined_text, re.IGNORECASE | re.MULTILINE):
            val_str = m.group(1).replace(",", "")
            try:
                val_f = float(val_str)
                if 20 <= val_f <= 25000 and val_f not in candidates:
                    candidates.append(val_f)
            except ValueError:
                pass

    if candidates:
        # Remove truncated OCR prefix artifacts (e.g. 90 when 900 or 901 was also read at another scale)
        int_strs = [str(int(c)) for c in candidates if float(c).is_integer()]
        filtered = []
        for c in candidates:
            s_c = str(int(c)) if float(c).is_integer() else ""
            if s_c and any(other != s_c and other.startswith(s_c) for other in int_strs):
                continue
            filtered.append(c)
        if not filtered:
            filtered = candidates

        # Prefer multiples of 5 (net transfer amount before 1 EGP Vodafone Cash fee, e.g. 900 over 901)
        mult5 = [c for c in filtered if float(c).is_integer() and int(c) % 5 == 0]
        if mult5:
            paid_amount = mult5[0]
        else:
            paid_amount = filtered[0]
            # If Vodafone Cash receipt shows 'الرسوم' and total ends in 1 (e.g. 901 = 900 + 1 fee)
            if "الرسوم" in combined_text and float(paid_amount).is_integer() and (int(paid_amount) - 1) % 5 == 0:
                paid_amount = float(int(paid_amount) - 1)

    if paid_amount is None:
        for i, line in enumerate(en_lines):
            u = line.strip().upper()
            if "EGP" in u:
                nums = re.findall(r"\d{2,5}(?:,\d{3})*(?:\.\d+)?", line)
                if nums:
                    paid_amount = float(nums[0].replace(",", ""))
                    break
                elif i > 0:
                    prev_nums = re.findall(r"\d{2,5}(?:,\d{3})*(?:\.\d+)?", en_lines[i - 1])
                    if prev_nums:
                        paid_amount = float(prev_nums[0].replace(",", ""))
                        break
            elif "VF-CASH" in u and i + 1 < len(en_lines):
                nxt = en_lines[i + 1].strip()
                if re.match(r"^\d{2,5}(?:\.\d+)?$", nxt):
                    paid_amount = float(nxt)
                    break

    # 3. Extract Receiving Wallet Phone & Sender Info
    all_phones = []
    for p in re.findall(r"(?:00201|\+?201|01)[0125]\d{8}", combined_text):
        cp = clean_phone(p)
        if cp and cp not in all_phones:
            all_phones.append(cp)

    instapay_handle = ""
    ipa_m = re.search(r"([a-zA-Z0-9._\-]+@instapay)", en_text, re.IGNORECASE)
    if ipa_m:
        instapay_handle = ipa_m.group(1)

    card_mask = ""
    card_m = re.search(r"CARD\s*[\*\s]*(\d{4})", combined_text, re.IGNORECASE)
    if card_m:
        card_mask = f"CARD ****{card_m.group(1)}"

    receiving_wallet_phone = ""
    receiving_wallet_name = ""
    sender_identifier = ""

    if len(all_phones) == 1:
        receiving_wallet_phone = all_phones[0]
        sender_identifier = instapay_handle or card_mask
    elif len(all_phones) >= 2:
        sender_identifier = all_phones[0]
        receiving_wallet_phone = all_phones[1]

    if receiving_wallet_phone:
        for i, line in enumerate(en_lines):
            if receiving_wallet_phone in normalize_digits(line) and i > 0:
                candidate = en_lines[i - 1].strip()
                if re.match(r"^[A-Za-z\s\.\*]+$", candidate) and "CARD" not in candidate.upper():
                    receiving_wallet_name = candidate
                    break

    # 4. Extract Reference / Transfer Code (10-16 digits that is NOT a phone number)
    transfer_code = ""
    for num in re.findall(r"\b\d{10,16}\b", en_text):
        if not re.match(r"^(?:00201|201|01)[0125]\d{8}$", num):
            transfer_code = num
            break

    # 5. Extract Date & Time
    paid_date = ""
    paid_time = ""
    raw_datetime_str = ""

    cleaned_en_for_date = re.sub(r"\bII\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", r"11 \1", en_text, flags=re.IGNORECASE)
    dt_m = re.search(
        r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+202\d)\s*(\d{1,2}:\d{2}\s*(?:AM|PM))?",
        cleaned_en_for_date,
        re.IGNORECASE
    )
    if dt_m:
        raw_datetime_str = dt_m.group(0).strip()
        date_part = dt_m.group(1).strip()
        time_part = (dt_m.group(2) or "").strip()
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                paid_date = datetime.strptime(date_part, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass
        paid_time = time_part
    else:
        num_dt = re.search(r"\b(202\d[\-/]\d{1,2}[\-/]\d{1,2}|\d{1,2}[\-/]\d{1,2}[\-/](?:20)?2\d)\b", combined_text)
        if num_dt:
            raw_datetime_str = num_dt.group(1)
            parts = re.split(r"[\-/]", raw_datetime_str)
            if len(parts) == 3:
                if len(parts[0]) == 4:
                    paid_date = f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                else:
                    yr = parts[2] if len(parts[2]) == 4 else f"20{parts[2]}"
                    paid_date = f"{yr}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            else:
                paid_date = raw_datetime_str
        tm_m = re.search(r"\b(\d{1,2}:\d{2}\s*(?:AM|PM|ص|م)?)\b", combined_text, re.IGNORECASE)
        if tm_m:
            paid_time = tm_m.group(1).strip()
            if raw_datetime_str and paid_time not in raw_datetime_str:
                raw_datetime_str = f"{paid_date or raw_datetime_str} {paid_time}".strip()

    paid_time_24h = format_time_24h(paid_time or raw_datetime_str)

    return {
        "provider": provider,
        "paid_amount": paid_amount,
        "receiving_wallet_phone": receiving_wallet_phone,
        "receiving_wallet_name": receiving_wallet_name,
        "sender_identifier": sender_identifier,
        "transfer_code": transfer_code,
        "paid_date": paid_date,
        "paid_time": paid_time_24h,
        "raw_datetime": raw_datetime_str or paid_time_24h,
        "ocr_en_lines": en_lines,
        "ocr_ar_lines": ar_lines,
    }


def calculate_expected_price(student_info: dict, prices_data: dict, paid_amount: float = None) -> dict:
    """
    Matches student_info against prices.json rules (including Content vs Class,
    special subject prices like French/German and Baccalaureate, and Sibling Discounts).
    """
    rules = prices_data.get("rules", [])
    grade = student_info.get("grade_code", "")
    edu_type = student_info.get("education_type", "عام")
    if edu_type == "مشترك":
        edu_type = "عام"
    duration = student_info.get("duration", "شهر")
    pkg_tier = student_info.get("package_tier", "Content")
    subjects = student_info.get("subjects", [])
    subj_count = len(subjects)
    is_bundle = student_info.get("is_full_bundle", False) or (subj_count == 0)
    is_sibling = student_info.get("is_sibling", False)

    matched_rule = None
    lookup_grade = "S1" if (grade == "BC" and is_bundle) else grade
    for rule in rules:
        rule_grades = [g.strip() for g in rule.get("grade", "").split(",")]
        rule_tier = rule.get("package_tier", "Content")
        if lookup_grade in rule_grades and rule.get("education_type") == edu_type and rule.get("duration") == duration and rule_tier == pkg_tier:
            matched_rule = rule
            break

    if not matched_rule:
        return {
            "matched_rule_id": None,
            "expected_price": None,
            "sibling_price": None,
            "explanation": "لم يتم العثور على قاعدة سعر مطابقة في جدول الأسعار الحالي"
        }

    special_map = matched_rule.get("special_subject_prices") or {}
    unit_price = matched_rule.get("unit_price")
    bundle_price = matched_rule.get("all_subjects_price")
    sibling_price = matched_rule.get("sibling_bundle_price")

    if is_bundle:
        if is_sibling and sibling_price is not None:
            expected = sibling_price
            reason = f"كل المواد بخصم الإخوات ({matched_rule.get('description')})"
        elif paid_amount is not None and sibling_price is not None and abs(float(paid_amount) - float(sibling_price)) < 0.01:
            expected = sibling_price
            reason = f"مطابق لسعر كل المواد بخصم الإخوات ({sibling_price} ج)"
        else:
            expected = bundle_price
            reason = f"باقة كل المواد ({matched_rule.get('description')})"
    else:
        total_subj_price = 0
        parts = []
        for s in subjects:
            sp = special_map.get(s, unit_price)
            if sp is not None:
                total_subj_price += float(sp)
                parts.append(f"{s}: {sp} ج")
        expected = total_subj_price if total_subj_price > 0 else bundle_price
        reason = f"{subj_count} مواد منفردة ({' + '.join(parts)})" if parts else matched_rule.get("description", "")

    return {
        "matched_rule_id": matched_rule.get("id"),
        "expected_price": float(expected) if expected is not None else None,
        "sibling_price": float(sibling_price) if sibling_price is not None else None,
        "bundle_price": float(bundle_price) if bundle_price is not None else None,
        "unit_price": float(unit_price) if unit_price is not None else None,
        "explanation": reason
    }
