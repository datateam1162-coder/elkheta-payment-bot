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

import re
import time
import threading
from datetime import datetime
from playwright.sync_api import sync_playwright
from parser import SYSTEM_SUBJECT_BUTTON_MAP, format_time_24h, clean_student_name_str, clean_phone


def find_chrome():
    """Locate Google Chrome executable on Windows."""
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    candidates.insert(0, winreg.QueryValue(k, None))
            except OSError:
                pass
    except ImportError:
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class ElkhetaPaymentBot:
    """
    Playwright controller for https://payments.elkheta.org
    Runs all operations in a dedicated lock to keep Playwright sync API thread-safe.
    """
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.session_dir = os.path.join(base_dir, "Browser_Session_Chrome")
        self._lock = threading.Lock()
        self.logged_in = False
        self.current_user = ""
        self.agent_name = ""
        self.available_wallets = []

    def _ensure_logged_in(self, page, username: str = "", password: str = ""):
        """If the current page is on /login, sign in using stored or provided credentials."""
        if "login" in page.url.lower() or page.locator("input[type='password']").count() > 0:
            u = (username or self.current_user).strip()
            if u and password:
                phone_input = page.locator("input[type='text'], input[type='tel'], input[name*='phone'], input[placeholder*='01']").first
                pass_input = page.locator("input[type='password']").first
                phone_input.fill(u)
                pass_input.fill(password.strip())
                submit_btn = page.locator("button[type='submit'], button:has-text('Sign In'), button:has-text('Login'), button:has-text('دخول')").first
                submit_btn.click()
                page.wait_for_url("**/payments**", timeout=30000)
                time.sleep(2)

    def login(self, username: str, password: str) -> dict:
        with self._lock:
            chrome_path = find_chrome()
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.session_dir,
                    headless=False,
                    executable_path=chrome_path,
                    viewport={"width": 1440, "height": 900}
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://payments.elkheta.org/payments?tab=all-payments", wait_until="domcontentloaded", timeout=60000)
                time.sleep(2)

                self._ensure_logged_in(page, username, password)
                self.logged_in = True
                self.current_user = username.strip()

                wallets = []
                try:
                    action_btn = page.locator("button:has-text('Action')").first
                    if action_btn.count() > 0:
                        action_btn.click()
                        time.sleep(0.5)
                        act_opt = page.locator("[role='menuitem']:has-text('Activate'), button:has-text('Activate')").first
                        if act_opt.count() > 0:
                            act_opt.click()
                            time.sleep(0.8)
                            dialog = page.locator("[role='dialog']").first
                            ws_btn = dialog.locator("button:has(h3:has-text('Wallet Selection')), h3:has-text('Wallet Selection')").first
                            if ws_btn.count() > 0:
                                ws_btn.click()
                                time.sleep(0.4)
                            wallet_texts = dialog.inner_text()
                            found_w = re.findall(r"(?:01[0125]\d{8})", wallet_texts)
                            wallets = list(dict.fromkeys(found_w))
                            close_btn = dialog.locator("button:has-text('Cancel')").first
                            if close_btn.count() > 0:
                                close_btn.click()
                except Exception:
                    pass

                self.available_wallets = wallets
                context.close()

                return {
                    "success": True,
                    "username": self.current_user,
                    "available_wallets": self.available_wallets,
                    "message": "تم تسجيل الدخول بنجاح والربط مع سيستم المدفوعات"
                }

    def search_and_analyze(self, student_info: dict, receipt_info: dict, price_info: dict, username: str = "", password: str = "") -> dict:
        """
        1. Searches in All Payments by:
           - student_phone
           - parent_phone
           - transfer_phone
           - transfer_code
        2. Checks Remaining Balance for student_phone / parent_phone.
        3. Applies the decision engine & hard-case detection rules.
        """
        search_history = []
        found_orders = []
        remaining_records = []

        students_list = student_info.get("students_list") or []
        raw_st_phone = student_info.get("student_phone", "")
        st_phones = []
        for p in re.findall(r"01[0125]\d{8}", str(raw_st_phone)):
            if p not in st_phones:
                st_phones.append(p)
        for st in students_list:
            cp = clean_phone(st.get("student_phone", ""))
            if re.match(r"^01[0125]\d{8}$", cp) and cp not in st_phones:
                st_phones.append(cp)

        student_phone = st_phones[0] if st_phones else clean_phone(raw_st_phone)
        parent_phone = clean_phone(student_info.get("parent_phone", ""))
        transfer_phone = clean_phone(student_info.get("transfer_phone", "") or receipt_info.get("sender_identifier", ""))
        transfer_code = (receipt_info.get("transfer_code", "") or "").strip()

        search_queries = []
        for idx_p, sp in enumerate(st_phones):
            if len(st_phones) == 1:
                lbl = "رقم الطالب"
            else:
                st_nm = ""
                if idx_p < len(students_list):
                    full_nm = (students_list[idx_p].get("student_name") or "").strip()
                    st_nm = full_nm.split()[0] if full_nm else ""
                lbl = f"رقم الطالب ({st_nm})" if st_nm else f"رقم الطالب ({idx_p + 1})"
            search_queries.append((lbl, sp))
        if parent_phone and parent_phone not in [q[1] for q in search_queries]:
            search_queries.append(("رقم ولي الأمر", parent_phone))
        if transfer_phone and re.match(r"^01[0125]\d{8}$", transfer_phone) and transfer_phone not in [q[1] for q in search_queries]:
            search_queries.append(("الرقم المحول منه", transfer_phone))
        if transfer_code and transfer_code not in [q[1] for q in search_queries]:
            search_queries.append(("كود التحويل المرجعي", transfer_code))

        with self._lock:
            chrome_path = find_chrome()
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.session_dir,
                    headless=False,
                    executable_path=chrome_path,
                    viewport={"width": 1440, "height": 900}
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://payments.elkheta.org/payments?tab=all-payments", wait_until="domcontentloaded", timeout=60000)
                time.sleep(2.2)
                self._ensure_logged_in(page, username, password)

                try:
                    all_tab = page.locator("[role='tab']:has-text('All Payments'), button:has-text('All Payments')").first
                    if all_tab.count() > 0:
                        all_tab.click()
                        time.sleep(1.0)
                except Exception:
                    pass

                search_input = page.locator("input[placeholder*='Search by name, phone, transfer']").first

                for label, q_val in search_queries:
                    if search_input.count() > 0:
                        search_input.fill("")
                        time.sleep(0.2)
                        search_input.fill(q_val)
                        search_input.press("Enter")
                        time.sleep(2.0)

                        page_text = page.locator("main, body").first.inner_text()
                        has_zero = bool(re.search(r"Showing\s*[\·\s]*0\s*[\·\s]*payment", page_text, re.IGNORECASE)) or ("No payments yet" in page_text)

                        if not has_zero:
                            rows = page.locator("table tbody tr").all()
                            for r in rows:
                                # Ensure row is a real data row with Edit/View buttons
                                if r.locator("button:has-text('Edit'), button[title='Edit'], button:has-text('View')").count() == 0:
                                    continue
                                row_txt = r.inner_text().strip()
                                if not row_txt or "No payments" in row_txt:
                                    continue
                                is_without_data = "Without Data" in row_txt
                                is_discrepancy = "Cost Discrepancy" in row_txt or "Remaining" in row_txt
                                date_m = re.search(r"(202\d[\-/]\d{1,2}[\-/]\d{1,2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+202\d|\d{1,2}\s+[A-Za-z]{3}\s+202\d)", row_txt)
                                order_date_str = date_m.group(1) if date_m else ""

                                paid_amt = receipt_info.get("paid_amount")
                                row_amount_matches = False
                                if paid_amt is not None:
                                    amt_int = str(int(float(paid_amt)))
                                    amt_fmt = f"{int(float(paid_amt)):,}"
                                    if f"EGP {amt_int}" in row_txt or f"EGP {amt_fmt}" in row_txt or f"Copy {amt_int}" in row_txt or amt_int in row_txt:
                                        row_amount_matches = True

                                found_orders.append({
                                    "matched_by": label,
                                    "query": q_val,
                                    "raw_row": row_txt,
                                    "type": "Without Data" if is_without_data else "With Data",
                                    "has_discrepancy": is_discrepancy,
                                    "row_amount_matches": row_amount_matches,
                                    "order_date": order_date_str
                                })

                        search_history.append({
                            "label": label,
                            "query": q_val,
                            "found_count": len([o for o in found_orders if o["query"] == q_val])
                        })

                # Check Remaining Balance page
                try:
                    page.goto("https://payments.elkheta.org/remaining-balance", wait_until="domcontentloaded", timeout=30000)
                    time.sleep(1.5)
                    rem_search = page.locator("input[type='text'], input[placeholder*='Search']").first
                    for label, q_val in [("رقم الطالب", student_phone), ("رقم ولي الأمر", parent_phone)]:
                        if not q_val or rem_search.count() == 0:
                            continue
                        rem_search.fill(q_val)
                        rem_search.press("Enter")
                        time.sleep(1.2)
                        rem_rows = page.locator("table tbody tr").all()
                        for rr in rem_rows:
                            rtxt = rr.inner_text().strip()
                            if rtxt and "No " not in rtxt and q_val in rtxt:
                                remaining_records.append({
                                    "matched_by": label,
                                    "query": q_val,
                                    "raw_row": rtxt
                                })
                except Exception:
                    pass

                context.close()

        # --- DECISION ENGINE & HARD CASE CHECKS ---
        hard_case_reasons = []
        proposed_action = "ACTIVATE_NEW_WITH_DATA"
        proposed_action_ar = "تسجيل اشتراك جديد (Activate -> With Data)"
        matched_without_data_query = ""

        without_data_matches = [o for o in found_orders if o["type"] == "Without Data"]
        with_data_matches = [o for o in found_orders if o["type"] == "With Data"]

        exact_without_data = [
            o for o in without_data_matches
            if o.get("row_amount_matches") or o.get("matched_by") == "كود التحويل المرجعي"
        ]

        if exact_without_data:
            matched_without_data_query = exact_without_data[0].get("query", "")
            if student_info.get("is_upgrade_note"):
                proposed_action = "CONVERT_WITHOUT_DATA_TO_UPGRADE"
                proposed_action_ar = f"تعديل أوردر Without Data ({matched_without_data_query}) وتحويله إلى Convert To Upgrade (الحالة 7 في الدوكس)"
                hard_case_reasons.append("مذكور في بيانات الطالب أنه Upgrade لأوردر سابق مع وجود عملية Without Data مطابقة.")
            else:
                proposed_action = "COMPLETE_WITHOUT_DATA"
                proposed_action_ar = f"الدخول على أوردر Without Data المطابق ({matched_without_data_query}) واستكمال بياناته وتحويله إلى With Data (الحالة 5 في الدوكس)"
        elif without_data_matches:
            matched_without_data_query = without_data_matches[0].get("query", "")
            hard_case_reasons.append(
                f"تم العثور على أوردر Without Data بنفس الرقم ({without_data_matches[0].get('matched_by')}: {matched_without_data_query}) ولكن بمبلغ مختلف عن الاسكرين."
            )
        elif with_data_matches:
            latest_order = with_data_matches[0]
            days_old = 999
            if latest_order.get("order_date"):
                for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d/%m/%Y"):
                    try:
                        dt = datetime.strptime(latest_order["order_date"], fmt)
                        days_old = abs((datetime.now() - dt).days)
                        break
                    except ValueError:
                        pass

            has_rem_order = any(o.get("has_discrepancy") for o in with_data_matches)
            if has_rem_order or student_info.get("is_final_payment_note"):
                proposed_action = "FINAL_PAYMENT_COMPLETE"
                proposed_action_ar = f"استكمال مبلغ ناقص (Final Payment) — الدخول Edit على الأوردر وإضافة المبلغ في Final Amount (الحالة 11 في الدوكس)"
                hard_case_reasons.append(
                    f"تم العثور على أوردر سابق عليه مبلغ متبقي (Remaining) أو مذكور في الشيت أنه استكمال باقي المبلغ ({latest_order.get('query')})."
                )
            elif student_info.get("is_upgrade_note"):
                proposed_action = "UPGRADE_EXISTING_ORDER"
                proposed_action_ar = f"ترقية باقة (Action -> Upgrade) للأوردر السابق ({latest_order.get('query')}) وحساب فرق السعر (الحالة 6 في الدوكس)"
                hard_case_reasons.append(
                    f"مذكور في نص الشيت أنه ترقية باقة (Upgrade / دفع الفرق) مع وجود أوردر سابق للطالب ({latest_order.get('query')})."
                )
            elif days_old >= 25:
                proposed_action = "RENEWAL_ACTIVATE_WITH_DATA"
                proposed_action_ar = f"تجديد اشتراك (أوردر قديم منذ {days_old} يوم) -> تسجيل اشتراك جديد With Data"
            else:
                if receipt_info.get("paid_amount") and price_info.get("expected_price") and float(receipt_info["paid_amount"]) < float(price_info["expected_price"]):
                    proposed_action = "UPGRADE_EXISTING_ORDER"
                    proposed_action_ar = f"احتمال ترقية باقة (Action -> Upgrade) أو استكمال دفع (Final Payment) لأوردر حديث ({latest_order.get('query')})"
                hard_case_reasons.append(
                    f"تم العثور على أوردر With Data حديث لنفس الرقم ({latest_order.get('matched_by')}: {latest_order.get('query')}) - احتمال تكرار أو Upgrade أو Final Payment."
                )
        else:
            if student_info.get("is_upgrade_note"):
                proposed_action = "UPGRADE_EXISTING_ORDER"
                proposed_action_ar = "ترقية باقة (Action -> Upgrade) — مذكور في الشيت أنه دفع فرق سعر لترقية الباقة (الحالة 6 في الدوكس)"
                hard_case_reasons.append("مذكور في نص الشيت أن الطالب يدفع فرق سعر لترقية الباقة (Upgrade).")
            elif student_info.get("is_final_payment_note"):
                proposed_action = "FINAL_PAYMENT_COMPLETE"
                proposed_action_ar = "استكمال مبلغ ناقص (Final Payment — الحالة 11 في الدوكس)"
                hard_case_reasons.append("مذكور في نص الشيت أن التحويل هو استكمال باقي مبلغ (Final Payment).")
            elif remaining_records:
                proposed_action = "ACTIVATE_WITH_USED_BALANCE"
                proposed_action_ar = "تسجيل اشتراك جديد مع استخدام رصيد سابق (+ Add Used Balance من Remaining Balance — الحالة 4 في الدوكس)"

        # Check 1: Price Discrepancy
        paid_amount = receipt_info.get("paid_amount")
        expected_price = price_info.get("expected_price")
        if paid_amount is None:
            hard_case_reasons.append("لم يتمكن الكود من قراءة مبلغ التحويل بوضوح من صورة الاسكرين.")
        elif expected_price is None:
            hard_case_reasons.append(f"المبلغ المدفوع ({paid_amount} ج) لا يوجد له سعر مقابل في جدول الأسعار الحالي للمقارنة.")
        elif abs(float(paid_amount) - float(expected_price)) > 0.01:
            diff = float(paid_amount) - float(expected_price)
            if diff < 0:
                hard_case_reasons.append(
                    f"اختلاف السعر: المبلغ في الاسكرين ({paid_amount} ج) أقل من سعر الباقة في الجدول ({expected_price} ج) بفرق {abs(diff)} ج (خصم أو مبلغ ناقص)."
                )
            else:
                hard_case_reasons.append(
                    f"اختلاف السعر: المبلغ في الاسكرين ({paid_amount} ج) أكبر من سعر الباقة في الجدول ({expected_price} ج) بفرق +{diff} ج (يرحل للـ Remaining)."
                )

        # Check 2: Receiving Wallet clarity & Sheet Wallet comparison
        recv_wallet = clean_phone(receipt_info.get("receiving_wallet_phone", ""))
        sheet_wallet = clean_phone(student_info.get("sheet_wallet_phone", ""))
        if not recv_wallet and not sheet_wallet:
            hard_case_reasons.append("رقم المحفظة المستلمة غير واضح في صورة الاسكرين ولا في الشيت.")
        elif recv_wallet and sheet_wallet and recv_wallet != sheet_wallet:
            hard_case_reasons.append(
                f"عدم تطابق المحفظة: المحفظة في الاسكرين ({recv_wallet}) مختلفة عن المحفظة المكتوبة في الشيت ({sheet_wallet})."
            )

        # Check 3: Remaining Balance exists
        if remaining_records:
            hard_case_reasons.append(
                f"يوجد رصيد سابق للطالب في صفحة Remaining Balance ({len(remaining_records)} سجل)."
            )

        # Check 4: Student essentials
        if not student_info.get("student_name") or not student_info.get("student_phone") or not student_info.get("grade_code"):
            hard_case_reasons.append("بعض بيانات الطالب الأساسية (الاسم / الرقم / الصف الدراسي) غير مكتملة في النص.")

        is_hard_case = len(hard_case_reasons) > 0

        return {
            "is_hard_case": is_hard_case,
            "hard_case_reasons": hard_case_reasons,
            "proposed_action": proposed_action,
            "proposed_action_ar": proposed_action_ar,
            "matched_without_data_query": matched_without_data_query,
            "search_history": search_history,
            "found_orders": found_orders,
            "remaining_records": remaining_records,
        }

    def _expand_section(self, dialog, section_title: str, check_selector: str):
        """Expands a collapsible accordion section in New Payment / Edit Payment if check_selector is not visible."""
        try:
            target = dialog.locator(check_selector).first
            if target.count() > 0 and target.is_visible():
                return
        except Exception:
            pass
        try:
            sec_btn = dialog.locator(f"button:has(h3:has-text('{section_title}')), h3:has-text('{section_title}')").first
            if sec_btn.count() > 0:
                sec_btn.scroll_into_view_if_needed()
                sec_btn.click()
                time.sleep(0.4)
        except Exception:
            pass

    def _select_wallet_in_dialog(self, dialog, wallet_phone: str, wallet_tag: str = ""):
        """Selects or looks up the receiving wallet inside Wallet Selection section."""
        if not wallet_phone and not wallet_tag:
            return
        self._expand_section(dialog, "Wallet Selection", "input[placeholder*='Enter wallet number']")

        phone_clean = clean_phone(wallet_phone)
        phone_no_zero = phone_clean[1:] if phone_clean.startswith("0") else phone_clean

        def click_matching_wallet_button() -> bool:
            buttons = dialog.locator("button").all()
            for btn in buttons:
                try:
                    txt = btn.inner_text().strip()
                except Exception:
                    continue
                if not txt or txt in ("Find", "Cancel", "Add Product", "Add order", "Select Files", "Paste"):
                    continue
                if (phone_clean and phone_clean in txt) or (phone_no_zero and len(phone_no_zero) >= 10 and phone_no_zero in txt) or (wallet_tag and wallet_tag in txt):
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    time.sleep(0.4)
                    return True
            return False

        # 1. Try clicking if already listed in Wallet Selection
        if click_matching_wallet_button():
            return

        # 2. Look up wallet by number using 'Enter wallet number...' + 'Find'
        lookup_inp = dialog.locator("input[placeholder*='Enter wallet number']").first
        find_btn = dialog.locator("button:has-text('Find')").first
        if lookup_inp.count() > 0 and find_btn.count() > 0:
            for candidate in [phone_clean, phone_no_zero]:
                if not candidate:
                    continue
                try:
                    lookup_inp.scroll_into_view_if_needed()
                    lookup_inp.fill("")
                    time.sleep(0.15)
                    lookup_inp.fill(candidate)
                    time.sleep(0.3)
                    if not find_btn.is_disabled():
                        find_btn.click()
                        time.sleep(1.2)
                    if click_matching_wallet_button():
                        return
                except Exception:
                    pass

    def _click_package_button(self, dialog, is_full_bundle: bool, package_tier: str, duration_code: str):
        """
        Finds and clicks the exact Package * button inside Order Builder.
        Examples of actual button texts on payments.elkheta.org:
          Full bundle:
            - 'Content - Month', 'Content - Term', 'Content - Year'
            - 'Follow up - Month', 'Follow up - Term', 'Follow up - Year'
          Single subject:
            - 'Subject - Content -  Month', 'Subject - Content -  Term', 'Subject - Content - Year'
            - 'Subject -Followup -  Month', 'Subject - followup -  Term', 'Subject - followup - Year'
        """
        want_subject = not is_full_bundle
        want_followup = (package_tier.lower() in ("class", "followup", "follow up"))
        want_dur = duration_code.lower()  # 'month', 'term', or 'year'
        if "year" in want_dur or "annual" in want_dur:
            want_dur = "year"
        elif "term" in want_dur:
            want_dur = "term"
        else:
            want_dur = "month"

        buttons = dialog.locator("button").all()
        for btn in buttons:
            try:
                raw_txt = btn.inner_text().strip()
            except Exception:
                continue
            if not raw_txt:
                continue
            norm = re.sub(r"[\s\-_]+", "", raw_txt).lower()
            has_subject = norm.startswith("subject")
            has_followup = "followup" in norm
            has_content = "content" in norm
            has_dur = want_dur in norm

            if has_subject == want_subject and has_dur:
                if want_followup and has_followup:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    time.sleep(0.5)
                    return True
                elif not want_followup and has_content:
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    time.sleep(0.5)
                    return True
        return False

    def _click_subject_button(self, dialog, subj_code: str):
        """Clicks the Subject button (e.g. MATH, ENGLISH, SCIENCE) inside Order Builder."""
        candidates = SYSTEM_SUBJECT_BUTTON_MAP.get(subj_code, [subj_code])
        buttons = dialog.locator("button").all()
        for cand in candidates:
            cand_norm = cand.strip().upper()
            for btn in buttons:
                try:
                    t = btn.inner_text().strip()
                except Exception:
                    continue
                if t.upper() == cand_norm or (len(cand_norm) >= 4 and cand_norm in t.upper() and "SUBJECT" not in t.upper() and "S3-" not in t.upper()):
                    btn.scroll_into_view_if_needed()
                    btn.click()
                    time.sleep(0.4)
                    return True
        return False

    def _build_order_items(self, dialog, student_info: dict):
        """
        Completes the Order Builder section for 1 or more students (e.g. 2 siblings in the same transfer),
        supporting either a Full Bundle or 1+ Single Subjects per student, and setting Current Price when discounted.
        """
        self._expand_section(dialog, "Order Builder", "button:has-text('Add Product')")

        raw_students_list = student_info.get("students_list") or []
        if len(raw_students_list) <= 1:
            students_to_register = [student_info]
        else:
            students_to_register = [dict(s) for s in raw_students_list]
            # Sync any manual UI edits to names / phones if separated by '+' or '-'
            ui_names = [n.strip() for n in str(student_info.get("student_name", "")).split("+") if n.strip()]
            ui_phones = re.findall(r"01[0125]\d{8}", str(student_info.get("student_phone", "")))
            for i, st in enumerate(students_to_register):
                if i < len(ui_names):
                    st["student_name"] = ui_names[i]
                if i < len(ui_phones):
                    st["student_phone"] = ui_phones[i]

        item_index = 0
        for st in students_to_register:
            edu_type = st.get("education_type", "عام")
            section_code = st.get("section_code", "")
            grade_code = st.get("grade_code", "")
            edu_code = st.get("edu_code", "AR")
            package_tier = st.get("package_tier", "Content")
            duration_code = st.get("duration_code", "Month")
            subjects = st.get("subjects") or []
            is_full_bundle = st.get("is_full_bundle", True) or (len(subjects) == 0)

            student_name = clean_student_name_str(st.get("student_name", ""))
            raw_ph = str(st.get("student_phone", ""))
            ph_matches = re.findall(r"01[0125]\d{8}", raw_ph)
            student_phone = ph_matches[0] if ph_matches else clean_phone(raw_ph)

            items_to_add = [None] if is_full_bundle else subjects

            for subj_code in items_to_add:
                # 1. Ensure 'Order Builder' is expanded (clicking 'Add order' auto-collapses Order Builder!)
                self._expand_section(dialog, "Order Builder", "button:has-text('Add Product')")
                time.sleep(0.3)

                add_prod_btn = dialog.locator("button:has-text('Add Product')").first
                if add_prod_btn.count() > 0 and add_prod_btn.is_visible():
                    add_prod_btn.scroll_into_view_if_needed()
                    add_prod_btn.click()
                    time.sleep(0.5)

                # 2. Select Language (عام / لغات / مشترك)
                lang_btn = dialog.locator(f"button:text-is('{edu_type}'), button:has-text('{edu_type}')").first
                if lang_btn.count() > 0:
                    lang_btn.scroll_into_view_if_needed()
                    lang_btn.click()
                    time.sleep(0.4)

                # 3. Select Class / Section (e.g. S1-EN, M3-AR, S2-AR-Baccalaureate)
                class_candidates = []
                if section_code:
                    class_candidates.append(section_code)
                if grade_code and edu_code:
                    class_candidates.append(f"{grade_code}-{edu_code}")
                if grade_code == "BC":
                    class_candidates.extend([f"S2-{edu_code}-Baccalaureate", "Baccalaureate", f"S1-{edu_code}"])

                for c_code in class_candidates:
                    c_btn = dialog.locator(f"button:text-is('{c_code}'), button:has-text('{c_code}')").first
                    if c_btn.count() > 0:
                        c_btn.scroll_into_view_if_needed()
                        c_btn.click()
                        time.sleep(0.4)
                        break

                # 4. Select Package button (Content/Follow up - Month/Term/Year or Subject - ...)
                self._click_package_button(dialog, is_full_bundle=(subj_code is None), package_tier=package_tier, duration_code=duration_code)

                # 5. If Single Subject, select the Subject button
                if subj_code is not None:
                    self._click_subject_button(dialog, subj_code)

                # 6. Fill Student Name * and Student Phone *
                name_inp = dialog.locator("input[placeholder*='Enter student name' i], input[placeholder*='student name' i]").first
                if name_inp.count() > 0:
                    name_inp.scroll_into_view_if_needed()
                    name_inp.fill(student_name)
                    time.sleep(0.2)

                phone_inp = dialog.locator("input[placeholder*='01xxxxxxxxx' i]").first
                if phone_inp.count() > 0:
                    phone_inp.scroll_into_view_if_needed()
                    phone_inp.fill(student_phone)
                    time.sleep(0.2)

                # 7. Click 'Add order' button to commit the item into Order Items
                add_order_btn = dialog.locator("button:has-text('Add order')").first
                if add_order_btn.count() > 0:
                    add_order_btn.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    if not add_order_btn.is_disabled():
                        add_order_btn.click()
                        time.sleep(0.6)

                # 8. If student has a discounted price (e.g. Sibling Discount 800 instead of 1050), update Current Price
                st_expected = st.get("expected_price")
                st_bundle = st.get("bundle_price")
                if subj_code is None and st_expected is not None and (st.get("is_sibling") or (st_bundle and float(st_expected) < float(st_bundle))):
                    try:
                        self._expand_section(dialog, "Order Builder", "button:has-text('Add Product')")
                        time.sleep(0.25)
                        cur_price_inputs = dialog.locator(
                            "span:has-text('Current Price') ~ input, "
                            "label:has-text('Current Price') ~ input, "
                            "div:has(> span:has-text('Current Price')) input"
                        )
                        if cur_price_inputs.count() > item_index:
                            cp_inp = cur_price_inputs.nth(item_index)
                            cp_inp.scroll_into_view_if_needed()
                            cp_str = str(int(float(st_expected)) if float(st_expected).is_integer() else st_expected)
                            cp_inp.fill(cp_str)
                            time.sleep(0.2)
                    except Exception:
                        pass

                item_index += 1

    def execute_order(
        self,
        student_info: dict,
        receipt_info: dict,
        price_info: dict,
        action_type: str,
        receipt_path: str,
        auto_submit: bool = False,
        username: str = "",
        password: str = ""
    ) -> dict:
        """
        Executes the registration on https://payments.elkheta.org (either completing a Without Data order
        or opening Action -> Activate -> With Data and filling all sections).
        """
        with self._lock:
            chrome_path = find_chrome()
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=self.session_dir,
                    headless=False,
                    executable_path=chrome_path,
                    viewport={"width": 1440, "height": 900}
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://payments.elkheta.org/payments?tab=all-payments", wait_until="domcontentloaded", timeout=60000)
                time.sleep(2.2)
                self._ensure_logged_in(page, username, password)

                opened_edit_modal = False

                if action_type == "COMPLETE_WITHOUT_DATA":
                    # Try searching by transfer_phone, parent_phone, student_phone, transfer_code to find the Without Data row
                    candidate_queries = []
                    for q_cand in [
                        student_info.get("matched_without_data_query", ""),
                        clean_phone(student_info.get("transfer_phone", "")),
                        clean_phone(student_info.get("parent_phone", "")),
                        clean_phone(student_info.get("student_phone", "")),
                        receipt_info.get("transfer_code", ""),
                    ]:
                        if q_cand and q_cand not in candidate_queries:
                            candidate_queries.append(q_cand)

                    search_input = page.locator("input[placeholder*='Search by name, phone, transfer']").first
                    for q in candidate_queries:
                        if search_input.count() > 0:
                            search_input.fill("")
                            time.sleep(0.2)
                            search_input.fill(q)
                            search_input.press("Enter")
                            time.sleep(2.0)
                            edit_btn = page.locator("table tbody tr:has-text('Without Data') button:has-text('Edit'), table tbody tr:has-text('Without Data') button[title='Edit']").first
                            if edit_btn.count() > 0:
                                edit_btn.scroll_into_view_if_needed()
                                edit_btn.click()
                                time.sleep(1.0)
                                opened_edit_modal = True
                                break

                if not opened_edit_modal:
                    # Open Action -> Activate
                    action_btn = page.locator("button:has-text('Action')").first
                    action_btn.wait_for(state="visible", timeout=15000)
                    action_btn.click()
                    time.sleep(0.5)
                    activate_item = page.locator("[role='menuitem']:has-text('Activate'), button:has-text('Activate')").first
                    activate_item.click()
                    time.sleep(1.0)

                dialog = page.locator("[role='dialog']").first
                dialog.wait_for(state="visible", timeout=15000)

                # Step A: Ensure Payment Mode is 'With Data (Full Details)'
                try:
                    mode_cb = dialog.locator("button[role='combobox']").first
                    if mode_cb.count() > 0 and "Without Data" in mode_cb.inner_text():
                        mode_cb.scroll_into_view_if_needed()
                        mode_cb.click()
                        time.sleep(0.3)
                        with_data_opt = page.locator("[role='option']:has-text('With Data')").first
                        if with_data_opt.count() > 0:
                            with_data_opt.click()
                            time.sleep(0.5)
                except Exception:
                    pass

                # Step B: Wallet Selection (if creating New Payment or wallet not yet selected)
                target_wallet = clean_phone(student_info.get("sheet_wallet_phone", "") or receipt_info.get("receiving_wallet_phone", ""))
                target_tag = student_info.get("sheet_wallet_tag", "")
                if not opened_edit_modal and (target_wallet or target_tag):
                    self._select_wallet_in_dialog(dialog, target_wallet, target_tag)

                # Step C: Order Builder (Add Product -> Language -> Class -> Package -> Subject -> Student Details -> Add order)
                self._build_order_items(dialog, student_info)

                # Step D: Payment Details (Paid Amount, Paid Time, Parent Phone, Receipt Images)
                self._expand_section(dialog, "Payment Details", "input[placeholder*='01XXXXXXXXX']")
                try:
                    paid_amt = receipt_info.get("paid_amount")
                    if paid_amt is None:
                        paid_amt = price_info.get("expected_price")
                    if paid_amt is not None:
                        amt_inp = dialog.locator("input[placeholder='0.00'], input[type='number']").first
                        if amt_inp.count() > 0:
                            cur_val = ""
                            try:
                                cur_val = amt_inp.input_value().strip()
                            except Exception:
                                pass
                            if not opened_edit_modal or not cur_val or cur_val in ("0", "0.00"):
                                amt_inp.scroll_into_view_if_needed()
                                amt_str = str(int(float(paid_amt)) if float(paid_amt).is_integer() else paid_amt)
                                amt_inp.fill(amt_str)
                                time.sleep(0.2)

                    if not opened_edit_modal:
                        paid_time_24 = format_time_24h(receipt_info.get("paid_time", "") or receipt_info.get("raw_datetime", ""))
                        if paid_time_24:
                            # Locate Paid Time input (near '24-hour (HH:mm)')
                            time_inp = dialog.locator("input[type='time'], label:has-text('Paid Time') ~ input, label:has-text('Paid Time') ~ div input").first
                            if time_inp.count() == 0:
                                # Fallback: input immediately preceding '24-hour (HH:mm)'
                                time_inp = dialog.locator("div:has-text('24-hour (HH:mm)') input").last
                            if time_inp.count() > 0:
                                time_inp.scroll_into_view_if_needed()
                                time_inp.fill(paid_time_24)
                                time.sleep(0.2)

                    parent_ph = clean_phone(student_info.get("parent_phone", ""))
                    if parent_ph:
                        par_inp = dialog.locator("input[placeholder*='01XXXXXXXXX']").first
                        if par_inp.count() > 0:
                            par_inp.scroll_into_view_if_needed()
                            par_inp.fill(parent_ph)
                            time.sleep(0.2)

                    if not opened_edit_modal and receipt_path and os.path.isfile(receipt_path):
                        abs_rcpt = os.path.abspath(receipt_path)
                        file_inp = dialog.locator("input[type='file']").first
                        if file_inp.count() > 0:
                            file_inp.set_input_files(abs_rcpt)
                            time.sleep(1.0)
                        else:
                            select_files_btn = dialog.locator("button:has-text('Select Files')").first
                            if select_files_btn.count() > 0:
                                select_files_btn.scroll_into_view_if_needed()
                                with page.expect_file_chooser(timeout=5000) as fc_info:
                                    select_files_btn.click()
                                fc_info.value.set_files(abs_rcpt)
                                time.sleep(1.0)
                except Exception:
                    pass

                # Step E: Transfer Details (Transfer Number *)
                if not opened_edit_modal:
                    self._expand_section(dialog, "Transfer Details", "input[placeholder*='Enter transfer number']")
                    t_phone = (
                        clean_phone(student_info.get("transfer_phone", ""))
                        or clean_phone(receipt_info.get("sender_identifier", ""))
                        or clean_phone(student_info.get("parent_phone", ""))
                        or clean_phone(student_info.get("student_phone", ""))
                    )
                    if t_phone:
                        try:
                            tn_inp = dialog.locator("input[placeholder*='Enter transfer number']").first
                            if tn_inp.count() > 0:
                                tn_inp.scroll_into_view_if_needed()
                                tn_inp.fill(t_phone)
                                time.sleep(0.3)
                        except Exception:
                            pass

                # Capture a preview screenshot before submitting/closing
                preview_path = os.path.join(self.base_dir, "static", "last_preview.png")
                os.makedirs(os.path.dirname(preview_path), exist_ok=True)
                try:
                    page.screenshot(path=preview_path)
                except Exception:
                    pass

                if auto_submit:
                    submit_btn = dialog.locator(
                        "button:has-text('Submit Payment and Create System Requests'), "
                        "button:has-text('Update Payment'), "
                        "button:has-text('Submit Payment')"
                    ).first
                    if submit_btn.count() > 0 and not submit_btn.is_disabled():
                        submit_btn.scroll_into_view_if_needed()
                        submit_btn.click()
                        time.sleep(3.0)
                        try:
                            page.screenshot(path=preview_path)
                        except Exception:
                            pass
                        context.close()
                        return {
                            "success": True,
                            "submitted": True,
                            "preview_image": f"/static/last_preview.png?t={int(time.time())}",
                            "message": "تم ملء جميع الخانات وحفظ الأوردر على السيستم بنجاح! ✅"
                        }
                    else:
                        context.close()
                        return {
                            "success": False,
                            "submitted": False,
                            "preview_image": f"/static/last_preview.png?t={int(time.time())}",
                            "error": "زر الحفظ النهائي (Submit/Update) غير مفعل — راجع صورة المعاينة للتأكد من اكتمال كل الحقول الإجبارية."
                        }
                else:
                    context.close()
                    return {
                        "success": True,
                        "submitted": False,
                        "preview_image": f"/static/last_preview.png?t={int(time.time())}",
                        "message": "تم ملء الفورم على السيستم وحفظ صورة المعاينة بنجاح (اضغط تأكيد الإرسال للحفظ النهائي)."
                    }
