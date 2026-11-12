import os
import sys

for _extra_site in [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "Lib", "site-packages"),
    r"C:\Mahmmoud\Lib\site-packages",
    r"C:\Users\ROOT\AppData\Local\Programs\antigravity\Lib\site-packages",
    r"C:\Users\ROOT\AppData\Roaming\Python\Python314\site-packages",
    r"C:\Users\ROOT\.gemini\antigravity\scratch\whatsapp-sender\Lib\site-packages",
]:
    if os.path.isdir(_extra_site) and _extra_site not in sys.path:
        sys.path.insert(0, _extra_site)

import json
import uuid
import importlib
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
import parser
import playwright_bot
from playwright_bot import ElkhetaPaymentBot

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_DIR = os.path.join(BASE_DIR, "static")
PRICES_FILE = os.path.join(BASE_DIR, "prices.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"), static_folder=STATIC_DIR)
app.config["TEMPLATES_AUTO_RELOAD"] = True
bot = ElkhetaPaymentBot(BASE_DIR)


@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.route("/api/status", methods=["GET"])
def api_status():
    cfg = load_saved_config()
    return jsonify({
        "online": True,
        "username": cfg.get("username", ""),
        "sheet_url": cfg.get("sheet_url", ""),
    })


# In-memory store of analyzed cases for instant "Ignore & Execute"
CASES_STORE = {}


def load_prices() -> dict:
    if os.path.isfile(PRICES_FILE):
        with open(PRICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"updated_at": "", "rules": []}


def save_prices(data: dict):
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(PRICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_saved_config() -> dict:
    default_sheet = "https://docs.google.com/spreadsheets/d/17-knTd-jIZGhVIBUGgr8WUtppQsUIpNdEAyVssFlLc0/edit?gid=29340265#gid=29340265"
    default_cfg = {"username": "", "password": "", "sheet_url": default_sheet}
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update(data)
                if not default_cfg.get("sheet_url"):
                    default_cfg["sheet_url"] = default_sheet
        except Exception:
            pass
    return default_cfg


def save_config_updates(**kwargs) -> dict:
    cfg = load_saved_config()
    for k, v in kwargs.items():
        if v is not None:
            cfg[k] = v
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


@app.route("/")
def index():
    creds = load_saved_config()
    prices = load_prices()
    return render_template("index.html", creds=creds, prices=prices)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username or not password:
        return jsonify({"success": False, "message": "يرجى إدخال اليوزر والباسورد"}), 400

    try:
        save_config_updates(username=username, password=password)
        res = bot.login(username, password)
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ أثناء تسجيل الدخول: {str(e)}"}), 500


@app.route("/api/save-sheet", methods=["POST"])
def api_save_sheet():
    data = request.get_json(force=True)
    sheet_url = (data.get("sheet_url") or "").strip()
    if not sheet_url:
        return jsonify({"success": False, "message": "يرجى إدخال لينك الشيت"}), 400
    save_config_updates(sheet_url=sheet_url)
    return jsonify({"success": True, "sheet_url": sheet_url, "message": "تم حفظ لينك الشيت بنجاح (وضع القراءة فقط Read-Only)"})


def _run_full_pipeline(
    student_info: dict,
    receipt_path: str,
    receipt_url: str,
    do_live_search: bool,
    auto_submit: bool,
    row_number: int = None
) -> dict:
    """Shared pipeline for both Google Sheet Row registration and Manual Paste registration."""
    cfg = load_saved_config()
    username = cfg.get("username", "")
    password = cfg.get("password", "")

    if not receipt_path and student_info.get("drive_file_id"):
        dl_path = parser.download_drive_image(student_info["drive_file_id"], UPLOAD_DIR)
        if dl_path and os.path.isfile(dl_path):
            receipt_path = dl_path
            receipt_url = f"/uploads/{os.path.basename(dl_path)}"

    receipt_info = parser.parse_receipt_image(receipt_path) if receipt_path else {}

    if student_info.get("transfer_phone"):
        receipt_info["transfer_phone"] = student_info["transfer_phone"]
    if student_info.get("sheet_wallet_phone") and not receipt_info.get("receiving_wallet_phone"):
        receipt_info["receiving_wallet_phone"] = student_info["sheet_wallet_phone"]

    prices_data = load_prices()
    price_info = parser.calculate_expected_price(student_info, prices_data, receipt_info.get("paid_amount"))

    if do_live_search:
        decision = bot.search_and_analyze(student_info, receipt_info, price_info, username=username, password=password)
        if decision.get("matched_without_data_query"):
            student_info["matched_without_data_query"] = decision["matched_without_data_query"]
    else:
        reasons = []
        paid_amt = receipt_info.get("paid_amount")
        exp_price = price_info.get("expected_price")
        if paid_amt is None:
            reasons.append("لم يتم قراءة المبلغ من صورة الاسكرين.")
        elif exp_price is None:
            reasons.append("لم يتم العثور على سعر مقابل في جدول الأسعار.")
        elif abs(float(paid_amt) - float(exp_price)) > 0.01:
            reasons.append(f"اختلاف السعر: الاسكرين ({paid_amt} ج) مقابل جدول الأسعار ({exp_price} ج).")
        if not receipt_info.get("receiving_wallet_phone") and not student_info.get("sheet_wallet_phone"):
            reasons.append("رقم المحفظة المستلمة غير واضح في الاسكرين ولا في الشيت.")

        decision = {
            "is_hard_case": len(reasons) > 0,
            "hard_case_reasons": reasons,
            "proposed_action": "ACTIVATE_NEW_WITH_DATA",
            "proposed_action_ar": "تسجيل اشتراك جديد (Activate -> With Data)",
            "search_history": [],
            "found_orders": [],
            "remaining_records": []
        }

    case_id = uuid.uuid4().hex[:10]
    CASES_STORE[case_id] = {
        "case_id": case_id,
        "row_number": row_number,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "student_info": student_info,
        "receipt_info": receipt_info,
        "price_info": price_info,
        "decision": decision,
        "receipt_path": receipt_path,
        "receipt_url": receipt_url,
        "status": "NEEDS_REVIEW" if decision["is_hard_case"] else "READY"
    }

    execution_result = None
    if not decision["is_hard_case"] and do_live_search:
        execution_result = bot.execute_order(
            student_info=student_info,
            receipt_info=receipt_info,
            price_info=price_info,
            action_type=decision["proposed_action"],
            receipt_path=receipt_path,
            auto_submit=auto_submit,
            username=username,
            password=password
        )
        CASES_STORE[case_id]["status"] = "EXECUTED" if auto_submit else "PREVIEWED"

    return {
        "success": True,
        "case_id": case_id,
        "row_number": row_number,
        "student_info": student_info,
        "receipt_info": receipt_info,
        "price_info": price_info,
        "decision": decision,
        "receipt_url": receipt_url,
        "execution_result": execution_result
    }


@app.route("/api/process-row", methods=["POST"])
def api_process_row():
    """
    Fetches a specific Row Number from the Google Sheet (strictly READ-ONLY),
    downloads its Google Drive screenshot, parses all data, checks All Payments,
    and registers the order on payments.elkheta.org (or pauses on a Hard Case with Ignore button).
    """
    data = request.get_json(force=True)
    cfg = load_saved_config()
    sheet_url = (data.get("sheet_url") or cfg.get("sheet_url") or "").strip()
    row_num_raw = data.get("row_number")
    do_live_search = bool(data.get("do_live_search", True))
    auto_submit = bool(data.get("auto_submit", True))

    if not sheet_url:
        return jsonify({"success": False, "message": "يرجى وضع لينك الشيت (Google Sheet URL) أولاً."}), 400

    try:
        row_number = int(str(row_num_raw).strip())
    except Exception:
        return jsonify({"success": False, "message": "يرجى إدخال رقم Row صحيح (مثلاً: 15)."}), 400

    save_config_updates(sheet_url=sheet_url)

    try:
        importlib.reload(parser)
        importlib.reload(playwright_bot)
        bot.__class__ = playwright_bot.ElkhetaPaymentBot
        client_cells = data.get("client_row_cells")
        if client_cells and isinstance(client_cells, list) and any(str(c).strip() for c in client_cells):
            sheet_row = parser.build_sheet_row_dict(row_number, client_cells)
        else:
            sheet_row = parser.fetch_google_sheet_row(sheet_url, row_number, UPLOAD_DIR)
        student_info = parser.parse_student_text(sheet_row["combined_tsv"])

        # Ensure sheet columns explicitly populate student_info
        if sheet_row.get("drive_url"):
            student_info["drive_url"] = sheet_row["drive_url"]
        if sheet_row.get("drive_file_id"):
            student_info["drive_file_id"] = sheet_row["drive_file_id"]
        if sheet_row.get("sheet_wallet_raw"):
            student_info["sheet_wallet_raw"] = sheet_row["sheet_wallet_raw"]
        if sheet_row.get("sheet_wallet_phone"):
            student_info["sheet_wallet_phone"] = sheet_row["sheet_wallet_phone"]
        if sheet_row.get("sheet_wallet_tag"):
            student_info["sheet_wallet_tag"] = sheet_row["sheet_wallet_tag"]
        if sheet_row.get("sheet_transfer_number"):
            student_info["transfer_phone"] = sheet_row["sheet_transfer_number"]

        res = _run_full_pipeline(
            student_info=student_info,
            receipt_path="",
            receipt_url="",
            do_live_search=do_live_search,
            auto_submit=auto_submit,
            row_number=row_number
        )
        res["sheet_row"] = sheet_row
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "message": f"خطأ أثناء سحب أو تسجيل الـ Row رقم {row_number}: {str(e)}"}), 500


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    student_text = request.form.get("student_text", "").strip()
    do_live_search = request.form.get("do_live_search", "true").lower() == "true"
    auto_submit = request.form.get("auto_submit", "false").lower() == "true"

    receipt_file = request.files.get("receipt_image")
    receipt_path = ""
    receipt_url = ""

    if receipt_file and receipt_file.filename:
        ext = os.path.splitext(receipt_file.filename)[1] or ".jpg"
        fname = f"rcpt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{ext}"
        receipt_path = os.path.join(UPLOAD_DIR, fname)
        receipt_file.save(receipt_path)
        receipt_url = f"/uploads/{fname}"

    try:
        importlib.reload(parser)
        importlib.reload(playwright_bot)
        bot.__class__ = playwright_bot.ElkhetaPaymentBot
        student_info = parser.parse_student_text(student_text)
        res = _run_full_pipeline(
            student_info=student_info,
            receipt_path=receipt_path,
            receipt_url=receipt_url,
            do_live_search=do_live_search,
            auto_submit=auto_submit,
            row_number=None
        )
        return jsonify(res)
    except Exception as e:
        return jsonify({"success": False, "message": f"حدث خطأ أثناء الفحص: {str(e)}"}), 500


@app.route("/api/ignore-and-execute", methods=["POST"])
def api_ignore_and_execute():
    """
    Called when the user reviews a Hard Case and clicks 'Ignore (موافق - نفّذ)'.
    Allows overriding any field before executing on payments.elkheta.org.
    """
    data = request.get_json(force=True)
    case_id = data.get("case_id")
    auto_submit = bool(data.get("auto_submit", False))
    overrides = data.get("overrides") or {}

    if not case_id or case_id not in CASES_STORE:
        return jsonify({"success": False, "message": "لم يتم العثور على بيانات هذه العملية"}), 404

    cfg = load_saved_config()
    username = cfg.get("username", "")
    password = cfg.get("password", "")

    case_obj = CASES_STORE[case_id]
    student_info = case_obj["student_info"]
    receipt_info = case_obj["receipt_info"]
    price_info = case_obj["price_info"]
    action_type = overrides.get("proposed_action") or case_obj["decision"]["proposed_action"]

    for k in ("student_name", "student_phone", "parent_phone", "transfer_phone", "education_type", "section_code", "duration_code", "package_tier"):
        if k in overrides and overrides[k] is not None and str(overrides[k]).strip() != "":
            student_info[k] = overrides[k]
    for k in ("paid_amount", "receiving_wallet_phone", "transfer_code", "paid_date", "paid_time"):
        if k in overrides and overrides[k] is not None and str(overrides[k]).strip() != "":
            receipt_info[k] = overrides[k]

    try:
        importlib.reload(parser)
        importlib.reload(playwright_bot)
        bot.__class__ = playwright_bot.ElkhetaPaymentBot
        res = bot.execute_order(
            student_info=student_info,
            receipt_info=receipt_info,
            price_info=price_info,
            action_type=action_type,
            receipt_path=case_obj["receipt_path"],
            auto_submit=auto_submit,
            username=username,
            password=password
        )
        case_obj["status"] = "IGNORED_AND_EXECUTED" if auto_submit else "IGNORED_AND_PREVIEWED"
        return jsonify({
            "success": True,
            "case_id": case_id,
            "execution_result": res
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"حدث خطأ أثناء التنفيذ: {str(e)}"}), 500


@app.route("/api/prices", methods=["GET", "POST"])
def api_prices():
    if request.method == "GET":
        return jsonify(load_prices())
    data = request.get_json(force=True)
    save_prices(data)
    return jsonify({"success": True, "message": "تم حفظ وتحديث جدول الأسعار بنجاح!", "prices": load_prices()})


if __name__ == "__main__":
    print("====================================================")
    print("   Elkheta Auto-Payment System Running on Port 5055 ")
    print("   Open: http://127.0.0.1:5055                      ")
    print("====================================================")
    app.run(host="127.0.0.1", port=5055, debug=False)
