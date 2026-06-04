import os
import sys
import json
import time
import re
from pathlib import Path
from datetime import datetime
import yaml
import traceback

try:
    from playwright.sync_api import sync_playwright, Page, BrowserContext
except ImportError:
    pass

# 프로젝트 루트 경로 설정
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class HarvesterAgent:
    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(project_root, "config.yaml")
        self.config_path = Path(config_path)
        self.load_config()
        self.setup_logging()
        
    def load_config(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
            
        self.source_docs_dir = Path(os.path.join(os.path.dirname(self.config_path), self.cfg["paths"]["source_docs"]))
        self.error_log_path = Path(os.path.join(os.path.dirname(self.config_path), self.cfg["paths"]["error_log"]))
        self.checkpoint_path = Path(os.path.join(os.path.dirname(self.config_path), self.cfg["visual_agent"]["checkpoint_file"]))
        
        self.source_docs_dir.mkdir(parents=True, exist_ok=True)
        self.error_log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 설정 변수 로드
        self.gw_url = self.cfg["visual_agent"]["groupware_url"]
        self.cdp_port = self.cfg["visual_agent"]["chrome_debugging_port"]
        self.capture_interval = self.cfg["visual_agent"]["capture_interval"]
        self.click_delay = self.cfg["visual_agent"]["click_delay"]
        self.default_years = self.cfg["visual_agent"]["default_migration_years"]

    def setup_logging(self):
        def log_error(phase, error, doc_id="N/A", doc_title="N/A"):
            tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            log_entry = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "doc_id": doc_id,
                "doc_title": doc_title,
                "phase": phase,
                "error_type": type(error).__name__,
                "message": str(error),
                "stack_trace": tb_str
            }
            try:
                with open(self.error_log_path, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            except Exception as le:
                print(f"[Fatal Logger Error] {le}")
        self.log_error = log_error

    def get_checkpoint(self) -> dict:
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"year": None, "page": 1, "doc_index": 0}

    def save_checkpoint(self, year, page, doc_index):
        checkpoint = {"year": year, "page": page, "doc_index": doc_index}
        try:
            with open(self.checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Harvester Checkpoint Warning] Cannot save checkpoint: {e}")

    def clear_checkpoint(self):
        if self.checkpoint_path.exists():
            try:
                self.checkpoint_path.unlink()
            except Exception:
                pass

    def sanitize_filename(self, filename: str) -> str:
        """Windows 파일 금지문자 정규화 및 제목 40자 제한"""
        clean = re.sub(r'[\\/:*?"<>|,]', '_', filename)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if len(clean) > 40:
            clean = clean[:40]
        return clean

    def connect_browser(self, p_instance) -> tuple:
        """디버깅 포트(9222)로 열려있는 사용자 Chrome에 CDP 연결"""
        try:
            browser = p_instance.chromium.connect_over_cdp(f"http://127.0.0.1:{self.cdp_port}")
            context = browser.contexts[0]
            
            # window.print() 차단 및 그룹웨어 미입력 잠금 방지(UserPortalLock 우회, ResetSession 주기적 호출) 스크립트 등록
            context.add_init_script("""
                window.print = () => {};
                window.UserPortalLock = () => { console.log('Bypassed UserPortalLock'); };
                setInterval(() => {
                    try {
                        if (typeof ResetSession === 'function') {
                            ResetSession();
                        }
                    } catch(e) {}
                }, 30000);
            """)
            
            # 이미 열려있는 페이지들에 대해서도 잠금 방지 즉시 적용
            for pg in context.pages:
                try:
                    pg.evaluate("""() => {
                        window.UserPortalLock = () => { console.log('Bypassed UserPortalLock'); };
                        if (typeof ResetSession === 'function') {
                            ResetSession();
                        }
                    }""")
                except Exception:
                    pass
            
            # 모든 다이얼로그(Alert, Confirm 등) 자동 수락/닫기 등록
            context.on("dialog", lambda dialog: dialog.dismiss())
            
            # 그룹웨어 탭 탐색 (포털이나 결재 메인 페이지를 우선적으로 매칭)
            gw_page = None
            for page in context.pages:
                if "index_portal.aspx" in page.url or "index_approval.aspx" in page.url:
                    gw_page = page
                    break
            
            # 없으면 아무 그룹웨어 페이지나 선택
            if not gw_page:
                for page in context.pages:
                    if "gw.knuh.or.kr" in page.url or "172.25" in page.url:
                        gw_page = page
                        break
            
            # 없으면 새 페이지 생성하여 이동
            if not gw_page:
                print(f"[Harvester] Groupware page not found. Navigating to {self.gw_url}")
                gw_page = context.new_page()
                gw_page.goto(self.gw_url)
                
            # 만약 상세조회 창이 메인으로 잡혔다면 메인 포털로 강제 이동
            if "contDocView_Cross.aspx" in gw_page.url or "ConDocView" in gw_page.url:
                print("[Harvester] Detail page detected as main handle. Redirecting to main portal...")
                gw_page.goto("https://gw.knuh.or.kr/myoffice/ezportal/index_portal.aspx")
                gw_page.wait_for_load_state("networkidle")
                
            return browser, context, gw_page
        except Exception as e:
            print(f"\n[Harvester Error] 크롬 디버깅 포트({self.cdp_port}) 접속 실패.")
            print("  크롬을 완전히 종료 후 아래 명령어로 실행하십시오:")
            print(f"  chrome.exe --remote-debugging-port={self.cdp_port}")
            raise e

    def get_approval_frame(self, page: Page):
        """전자결재 시스템이 로드된 프레임을 찾아 반환합니다. 없으면 page 자체를 반환합니다."""
        for frame in page.frames:
            if "ezApprovalG" in frame.url or "index_approval" in frame.url or "Approval" in frame.url:
                return frame
        for frame in page.frames:
            if "__MultiViewPage" in frame.name:
                return frame
        return page

    def find_locator(self, page, selector):
        """페이지 본문 또는 프레임 내에서 해당 selector를 만족하고 visible한 첫번째 locator를 찾아 반환합니다."""
        loc = page.locator(selector).first
        if loc.is_visible():
            return loc
        for frame in page.frames:
            try:
                loc = frame.locator(selector).first
                if loc.is_visible():
                    return loc
            except Exception:
                pass
        return page.locator(selector).first

    def check_login_and_authenticate(self, page: Page):
        """로그인 여부 확인 및 자동 로그인 수행"""
        print("[Harvester] Checking login state...")
        title = page.title()
        url = page.url
        pw_exists = page.locator("input[type='password']").count() > 0
        print(f"[Harvester Debug] Title: '{title}', URL: '{url}', PW exists: {pw_exists}")
        # 로그인 페이지 여부 검사
        if "login" in title.lower() or "login" in url.lower() or pw_exists:
            print("[Harvester] Login page detected. Performing automated login...")
            # ID/PW 입력란 자동 탐색
            id_input = page.locator("input[type='text']").first
            pw_input = page.locator("input[type='password']").first
            login_btn = page.locator("button:has-text('LOGIN'), input[type='submit'], .btn_login, .LOGIN").first
            
            # 환경변수에서 로드
            username = os.environ.get("GROUPWARE_ID") or "10612"
            password = os.environ.get("GROUPWARE_PW") or "knuh1234*"
            
            if id_input.is_visible() and pw_input.is_visible():
                id_input.fill(username)
                pw_input.fill(password)
                time.sleep(self.click_delay)
                
                # 버튼 클릭 또는 엔터
                if login_btn.is_visible():
                    login_btn.click()
                else:
                    pw_input.press("Enter")
                    
                page.wait_for_load_state("networkidle")
                time.sleep(2)
            else:
                raise ValueError("로그인 입력 필드를 찾지 못했습니다.")
        else:
            print("[Harvester] Already logged in.")

    def check_and_unlock_screen(self, page: Page) -> bool:
        """미사용으로 인한 그룹웨어 잠금 화면(Main_iFrameUserLockLayer) 및 세션 만료 페이지(Login.aspx) 자동 점검 및 해제"""
        try:
            # 1. 세션 만료로 Login.aspx로 튕겼거나 비밀번호 입력창이 메인에 보이면 재로그인 수행
            title = page.title()
            url = page.url
            pw_input_main = page.locator("input[type='password']").first
            
            is_logged_out = (
                "login" in title.lower() or 
                "login" in url.lower() or 
                (pw_input_main.count() > 0 and pw_input_main.is_visible())
            )
            
            if is_logged_out:
                print("[Harvester Authenticator] Session expired or logged out. Re-authenticating...")
                self.check_login_and_authenticate(page)
                self.navigate_to_approval_system(page)
                return True
                
            # 2. 장시간 미사용 잠금 레이어 감지
            lock_frame = None
            for frame in page.frames:
                if "Main_iFrameUserLockLayer" in frame.name or "CheckAccess" in frame.url:
                    lock_frame = frame
                    break
                    
            if lock_frame:
                print("[Harvester Lock Screen] Inactivity lock screen overlay detected! Unlocking...")
                pw_input = lock_frame.locator("input#CheckPW").first
                if pw_input.count() > 0:
                    password = os.environ.get("GROUPWARE_PW") or "knuh1234*"
                    pw_input.fill(password)
                    time.sleep(0.5)
                    
                    unlocked = lock_frame.evaluate("""() => {
                        let inp = document.getElementById('CheckPW');
                        if (inp) {
                            if (typeof confirm === 'function') {
                                confirm();
                                return "confirm_called";
                            } else {
                                let btn = document.querySelector("a[onclick='confirm()']");
                                if (btn) {
                                    btn.click();
                                    return "btn_clicked";
                                }
                            }
                        }
                        return "not_found";
                    }""")
                    print(f"[Harvester Lock Screen] JS Unlock Triggered: {unlocked}")
                    time.sleep(3.0)
                    
                    # 언락 후 확인
                    still_locked = False
                    for frame in page.frames:
                        if "Main_iFrameUserLockLayer" in frame.name or "CheckAccess" in frame.url:
                            try:
                                if frame.locator("input#CheckPW").count() > 0:
                                    still_locked = True
                                    break
                            except Exception: pass
                    
                    if still_locked:
                        print("[Harvester Lock Screen Error] Unlock attempt failed.")
                        return False
                    else:
                        print("[Harvester Lock Screen Success] Unlock successful.")
                        return True
                else:
                    print("[Harvester Lock Screen Warning] CheckPW input element not found in lock frame.")
                    
        except Exception as e:
            print(f"[Harvester Auth Error] Exception in check_and_unlock_screen: {e}")
            
        return False

    def navigate_to_approval_system(self, page: Page):
        """대시보드에서 '결재할 문서' 블록을 클릭하여 전자결재 화면 진입"""
        page.wait_for_load_state("load")
        time.sleep(1)
        
        # 전자결재 화면인지 확인
        approval_frame = self.get_approval_frame(page)
        if "ezApprovalG" in page.url or (approval_frame != page and "ezApprovalG" in approval_frame.url):
            print("[Harvester] Already inside Electronic Approval system.")
            return

        print("[Harvester] Navigating to Electronic Approval dashboard...")
        # '결재할 문서'를 포함하는 프레임 찾기
        target = page
        for frame in page.frames:
            if "ezPortal" in frame.url or "PortalLayout" in frame.url or "__MultiViewPage" in frame.name:
                target = frame
                break
                
        box = target.locator("text=결재할 문서").first
        if not box.is_visible():
            box = target.locator("div, a, td, span").filter(has_text=re.compile(r"결재할\s*문서")).first
        
        if not box.is_visible():
            box = target.locator(".결재할문서, .box_approval, [aria-label*='결재할 문서']").first

        if box.is_visible():
            box.click()
            page.wait_for_load_state("networkidle")
            time.sleep(self.click_delay)
        else:
            # 주소 강제 이동 시도
            fallback_url = f"http://127.0.0.1:59222/list.html" if "127.0.0.1" in page.url or "localhost" in page.url else "https://gw.knuh.or.kr/myoffice/ezportal/index_portal.aspx"
            print(f"[Harvester Warning] '결재할 문서' 블록을 찾지 못해 전자결재 경로로 강제 이동합니다: {fallback_url}")
            page.goto(fallback_url)
            time.sleep(self.click_delay)

    def select_menu_and_year(self, page: Page, menu_name="결재한문서", year_str=None):
        """좌측 트리 메뉴에서 대상 문서함을 선택하고 연도 필터 적용"""
        print(f"[Harvester] Selecting menu: {menu_name}")
        
        # 좌측 트리 메뉴 '결재한문서' 클릭
        menu_item = self.find_locator(page, f"text={menu_name}")
        
        if not menu_item.is_visible():
            menu_item = self.find_locator(page, f".menu_item:has-text('{menu_name}')")
            
        try:
            menu_item.click(timeout=3000)
            time.sleep(self.click_delay)
        except Exception as e:
            print(f"[Harvester Warning] '{menu_name}' 클릭 실패했으나 계속 진행합니다. ({e})")

        # 연도 필터 설정 (드롭다운)
        if year_str:
            print(f"[Harvester] Selecting year filter: {year_str}")
            dropdown = self.find_locator(page, "select")
            if dropdown.is_visible():
                dropdown.select_option(label=str(year_str))
                time.sleep(self.click_delay)
            else:
                print("[Harvester Warning] Year filter dropdown select tag not found.")

    def go_to_page(self, page: Page, target_page: int) -> bool:
        """현재 페이지에서 target_page로 이동합니다. 여러 블록을 건너뛰어야 할 수도 있습니다."""
        self.check_and_unlock_screen(page)
        target = self.get_approval_frame(page)
        
        # 10번 시도 제한
        for attempt in range(10):
            self.check_and_unlock_screen(page)
            # 현재 활성화된 페이지 확인
            active_page = target.evaluate("""() => {
                let el = document.querySelector('span.on');
                return el ? parseInt(el.innerText.trim()) : 1;
            }""")
            
            if active_page == target_page:
                print(f"[Harvester Pagination] Successfully arrived at page {target_page}")
                return True
                
            # target_page가 현재 표시되는 블록에 있는지 확인
            # 표시되는 페이지 목록 가져오기
            visible_pages = target.evaluate("""() => {
                let spans = Array.from(document.querySelectorAll('span[onclick*="GoToPageNavi"]'));
                return spans.map(s => parseInt(s.innerText.trim())).filter(n => !isNaN(n));
            }""")
            
            if not visible_pages:
                print("[Harvester Pagination Warning] No visible page buttons found.")
                return False
                
            if target_page in visible_pages:
                # 블록 내에 존재하므로 직접 클릭
                print(f"[Harvester Pagination] Page {target_page} is visible in current block. Clicking it...", flush=True)
                clicked = target.evaluate(f"() => {{ let spans = Array.from(document.querySelectorAll('span[onclick*=\\\"GoToPageNavi\\\"]')); let targetSpan = spans.find(s => parseInt(s.innerText.trim()) === {target_page}); if (targetSpan) {{ targetSpan.click(); return true; }} return false; }}")
                if clicked:
                    # Wait for transition to complete
                    transitioned = False
                    for _ in range(30): # 7.5 seconds max
                        time.sleep(0.25)
                        new_active = target.evaluate("""() => {
                            let el = document.querySelector('span.on');
                            return el ? parseInt(el.innerText.trim()) : 1;
                        }""")
                        if new_active == target_page:
                            transitioned = True
                            break
                    if transitioned:
                        continue
                    else:
                        print(f"[Harvester Pagination Warning] Page transition timed out after click.")
                        return False
                else:
                    print(f"[Harvester Pagination Warning] Failed to click page {target_page} span.")
                    return False
            else:
                # 현재 블록에 없음. 이전 블록 또는 다음 블록으로 이동해야 함
                if target_page > max(visible_pages):
                    # 다음 블록으로 이동
                    print(f"[Harvester Pagination] Target page {target_page} is in a later block. Clicking Next Block...", flush=True)
                    clicked_arrow = target.evaluate("""() => {
                        let btn = document.getElementById('span_Arrow_right');
                        if (btn && btn.parentElement && btn.parentElement.getAttribute('onclick')) {
                            btn.parentElement.click();
                            return true;
                        }
                        return false;
                    }""")
                    if not clicked_arrow:
                        print("[Harvester Pagination] Next block button is not available. Cannot progress.")
                        return False
                        
                    # Wait for block transition (active_page should change)
                    transitioned = False
                    for _ in range(30):
                        time.sleep(0.25)
                        new_active = target.evaluate("""() => {
                            let el = document.querySelector('span.on');
                            return el ? parseInt(el.innerText.trim()) : 1;
                        }""")
                        if new_active != active_page:
                            transitioned = True
                            break
                    if transitioned:
                        continue
                    else:
                        print("[Harvester Pagination Warning] Block transition timed out after next arrow click.")
                        return False
                else:
                    # 이전 블록으로 이동
                    print(f"[Harvester Pagination] Target page {target_page} is in an earlier block. Clicking Prev Block...", flush=True)
                    clicked_arrow = target.evaluate("""() => {
                        let btn = document.getElementById('span_Arrow_left');
                        if (btn && btn.parentElement && btn.parentElement.getAttribute('onclick')) {
                            btn.parentElement.click();
                            return true;
                        }
                        return false;
                    }""")
                    if not clicked_arrow:
                        print("[Harvester Pagination] Prev block button is not available. Cannot go back.")
                        return False
                        
                    # Wait for block transition (active_page should change)
                    transitioned = False
                    for _ in range(30):
                        time.sleep(0.25)
                        new_active = target.evaluate("""() => {
                            let el = document.querySelector('span.on');
                            return el ? parseInt(el.innerText.trim()) : 1;
                        }""")
                        if new_active != active_page:
                            transitioned = True
                            break
                    if transitioned:
                        continue
                    else:
                        print("[Harvester Pagination Warning] Block transition timed out after prev arrow click.")
                        return False
                    
        return False

    def parse_list_page_and_queue(self, page: Page) -> list:
        """현재 리스트 페이지의 문서 리스트를 파싱하여 작업 큐 빌드"""
        self.check_and_unlock_screen(page)
        target = self.get_approval_frame(page)
        
        # 테이블 데이터가 로드될 때까지 대기
        try:
            target.wait_for_selector("table tbody tr td, table.list_table tr td, table.list_table td", timeout=5000)
            time.sleep(1.5) # 추가 안정화 대기
        except Exception:
            pass
            
        queue = []
        # 테이블 행 순회
        rows = target.locator("table tbody tr, table.list_table tr").all()
        # 헤더 행 제외하고  tbody 내의 데이터 행 탐색
        headers = []
        
        # 헤더 파악
        header_cols = target.locator("table thead th, table.list_table th").all()
        if header_cols:
            headers = [col.inner_text().strip() for col in header_cols]
        else:
            # 1번째 행을 헤더로 추정
            first_row = target.locator("table tr").first
            if first_row.is_visible():
                headers = [col.inner_text().strip() for col in first_row.locator("td, th").all()]
                
        print(f"[Harvester] Detected columns: {headers}")
        
        # 인덱스 매핑
        col_indices = {
            "doc_number": -1,
            "title": -1,
            "drafter": -1,
            "department": -1,
            "date": -1,
            "doc_type": -1
        }
        
        for idx, h in enumerate(headers):
            if "문서번호" in h: col_indices["doc_number"] = idx
            elif "제목" in h: col_indices["title"] = idx
            elif "기안자" in h or "작성자" in h: col_indices["drafter"] = idx
            elif "기안부서" in h: col_indices["department"] = idx
            elif "기안일시" in h or "완료일시" in h: col_indices["date"] = idx
            elif "문서명" in h: col_indices["doc_type"] = idx

        # 행 데이터 파싱
        # 헤더가 아닌 데이터 tr만 필터링
        for row_idx, row in enumerate(rows):
            if not row.is_visible():
                continue
            # 체크박스나 아이콘만 있는 빈 행 제외
            cells = row.locator("td").all()
            if len(cells) < 4:
                continue
                
            # 1순위: colname 기반 파싱 시도
            doc_num = ""
            title = ""
            drafter = ""
            dept = ""
            date_val = ""
            doc_type = ""
            
            has_colname = False
            for cell in cells:
                try:
                    colname = cell.evaluate("el => el.getAttribute('colname')")
                    if colname:
                        has_colname = True
                        text = cell.inner_text().strip()
                        if colname == "DocNo": doc_num = text
                        elif colname == "DocTitle": title = text
                        elif colname == "WriterName": drafter = text
                        elif colname == "WriterDeptName": dept = text
                        elif colname in ["EndDate", "WriteDate"]: date_val = text
                        elif colname == "FormName": doc_type = text
                except Exception:
                    pass
            
            # 2순위: colname이 없거나 실패했을 때 기존 인덱스 방식 폴백
            if not has_colname or not title:
                row_text = [c.inner_text().strip() for c in cells]
                title_cell_idx = col_indices["title"] if col_indices["title"] != -1 else 1
                if title_cell_idx < len(cells):
                    title = row_text[title_cell_idx]
                doc_num = row_text[col_indices["doc_number"]] if col_indices["doc_number"] != -1 else ""
                drafter = row_text[col_indices["drafter"]] if col_indices["drafter"] != -1 else ""
                dept = row_text[col_indices["department"]] if col_indices["department"] != -1 else ""
                date_val = row_text[col_indices["date"]] if col_indices["date"] != -1 else ""
                doc_type = row_text[col_indices["doc_type"]] if col_indices["doc_type"] != -1 else ""

            # 완료일시 포맷 파싱 (YYYY-MM-DD)
            match = re.search(r'\d{4}-\d{2}-\d{2}', date_val)
            clean_date = match.group(0) if match else date_val[:10]

            queue.append({
                "index": len(queue),
                "row_index": row_idx,  # Stale Reference 방지를 위해 tr의 0-based 인덱스 저장
                "doc_number": doc_num,
                "title": title,
                "drafter": drafter,
                "department": dept,
                "date": clean_date,
                "doc_type": doc_type
            })
            
        return queue

    def download_pdf_via_cdp(self, context: BrowserContext, print_page: Page, doc_number: str, title: str) -> str:
        """인쇄 팝업 창에 대해 CDP를 연결하고 Page.printToPDF 호출하여 무인 직접 다운로드"""
        print("[Harvester CDP] Connecting CDP Session to Print Page...")
        try:
            # 로딩 완료 감지: 본문 서명란 또는 완료 텍스트 대기
            print_page.wait_for_load_state("domcontentloaded")
            time.sleep(2) # 리소스 로딩 안정화 대기
            
            cdp_session = context.new_cdp_session(print_page)
            print("[Harvester CDP] Generating PDF stream via Page.printToPDF...")
            
            pdf_result = cdp_session.send("Page.printToPDF", {
                "printBackground": True,
                "preferCSSPageSize": True,
                "displayHeaderFooter": False
            })
            
            import base64
            pdf_bytes = base64.b64decode(pdf_result["data"])
            
            # 용량 검증 (5KB 미만이면 비정상 공백 페이지로 간주하고 에러)
            if len(pdf_bytes) < 5000:
                raise ValueError("생성된 PDF의 크기가 너무 작습니다(5KB 미만). 로딩 미완료 의심.")
                
            safe_title = self.sanitize_filename(title)
            safe_num = self.sanitize_filename(doc_number) if doc_number else "NO_NUM"
            pdf_filename = f"{safe_num}_{safe_title}.pdf"
            pdf_path = self.source_docs_dir / pdf_filename
            
            pdf_path.write_bytes(pdf_bytes)
            print(f"[Harvester CDP Success] Selectable PDF downloaded directly: {pdf_filename}")
            return str(pdf_path)
            
        except Exception as e:
            self.log_error("CDP_PDF_GENERATION", e, doc_id=doc_number, doc_title=title)
            # 폴백: HTML outerHTML 스크랩
            print(f"[Harvester CDP Warning] CDP PDF 인쇄 실패. HTML 소스 스크랩 폴백을 작동합니다. 원인: {e}")
            try:
                html_text = print_page.content()
                safe_title = self.sanitize_filename(title)
                safe_num = self.sanitize_filename(doc_number) if doc_number else "NO_NUM"
                html_filename = f"{safe_num}_{safe_title}.html"
                html_path = self.source_docs_dir / html_filename
                html_path.write_text(html_text, encoding='utf-8')
                print(f"[Harvester Fallback Success] Raw HTML scraped instead: {html_filename}")
                return str(html_path)
            except Exception as fe:
                self.log_error("HTML_SCRAPE_FALLBACK", fe, doc_id=doc_number, doc_title=title)
                raise fe

    def download_attachments(self, detail_page: Page, doc_number: str) -> list:
        """결재문서 상세 화면에서 첨부파일 링크 감지 및 로컬 폴더 다운로드"""
        attachments = []
        try:
            # '첨부파일(N)' 텍스트 또는 탭 영역 탐색
            attach_tab = self.find_locator(detail_page, "text=첨부파일")
            if attach_tab.is_visible():
                attach_tab.click()
                time.sleep(1)
                
                # 첨부파일 목록 내부 링크/다운로드 버튼 스캔
                download_links = []
                # 메인 페이지 검색
                links = detail_page.locator(".attach_list a, #attach_list a, a[href*='download'], a[href*='Download']").all()
                if links:
                    download_links.extend(links)
                else:
                    links = detail_page.locator("a").filter(has_text=re.compile(r"\.(hwp|hwpx|pdf|xlsx|xls|docx|doc)$")).all()
                    download_links.extend(links)
                
                # 프레임 검색
                if not download_links:
                    for frame in detail_page.frames:
                        try:
                            links = frame.locator(".attach_list a, #attach_list a, a[href*='download'], a[href*='Download']").all()
                            if links:
                                download_links.extend(links)
                            else:
                                links = frame.locator("a").filter(has_text=re.compile(r"\.(hwp|hwpx|pdf|xlsx|xls|docx|doc)$")).all()
                                download_links.extend(links)
                        except Exception:
                            pass
                
                safe_num = self.sanitize_filename(doc_number) if doc_number else "NO_NUM"
                
                for idx, link in enumerate(download_links):
                    try:
                        filename = link.inner_text().strip()
                        if not filename:
                            filename = f"attachment_{idx+1}"
                            
                        print(f"   [Harvester] Attemping download attachment: {filename}")
                        
                        # Playwright 다운로드 이벤트 감수 (evaluate click으로 대기 프리징 방지)
                        with detail_page.expect_download(timeout=10000) as download_info:
                            link.evaluate("el => el.click()")
                        download = download_info.value
                        
                        # 저장명 구성: 첨부파일_문서번호_인덱스_파일명
                        safe_filename = self.sanitize_filename(filename)
                        dest_name = f"첨부파일_{safe_num}_{idx+1}_{safe_filename}"
                        dest_path = self.source_docs_dir / dest_name
                        
                        download.save_as(dest_path)
                        attachments.append(str(dest_path))
                        print(f"   [Harvester Success] Downloaded attachment: {dest_name}")
                        
                    except Exception as ae:
                        print(f"   [Harvester Warning] Failed to download individual attachment: {ae}")
                        
        except Exception as e:
            print(f"   [Harvester Warning] Attachment download process encountered error (skipped): {e}")
            
        return attachments

    def process_single_document(self, context: BrowserContext, gw_page: Page, doc_item: dict) -> bool:
        """단일 결재문서 더블클릭 -> 새창 상세조회 -> 인쇄 모달 클릭 -> CDP PDF 인쇄 -> 첨부파일 저장 -> 복귀 전과정 수행"""
        doc_num = doc_item["doc_number"]
        title = doc_item["title"]
        row_index = doc_item["row_index"]
        
        print(f"\n▶ [Harvester] Harvesting doc: {doc_num} | {title}")
        
        try:
            # 1. Stale Reference 방지를 위해 실시간으로 목록에서 행 및 링크 재쿼리
            target_frame = self.get_approval_frame(gw_page)
            rows = target_frame.locator("table tbody tr, table.list_table tr").all()
            if row_index >= len(rows):
                raise ValueError(f"행 인덱스({row_index})가 테이블 행 개수({len(rows)})를 초과했습니다.")
            
            row = rows[row_index]
            cells = row.locator("td").all()
            
            # colname="DocTitle" 기반으로 다시 식별하거나, 폴백으로 기존 인덱스 기반으로 쿼리
            title_link = None
            for cell in cells:
                try:
                    colname = cell.evaluate("el => el.getAttribute('colname')")
                    if colname == "DocTitle":
                        title_link = cell.locator("a, span.link").first
                        if not title_link.is_visible():
                            title_link = cell
                        break
                except Exception:
                    pass
            
            if not title_link or not title_link.is_visible():
                # 폴백: td 내부의 첫 a나 span 링크 찾기
                title_link = row.locator("td a, td span.link").first
                if not title_link.is_visible():
                    title_link = row.locator("td").nth(5) # 예비 컬럼 인덱스
            
            # 더블클릭 전 뷰포트 내 스크롤 위치 보정 추가
            title_link.scroll_into_view_if_needed()
            time.sleep(0.5)
            
            # 문서 제목 더블 클릭 (새 팝업 감수)
            with context.expect_page(timeout=15000) as popup_info:
                title_link.dblclick()
                
            detail_page = popup_info.value
            detail_page.wait_for_load_state("load")
            print("   [Harvester] Detail popup page loaded.")
            time.sleep(2)
            
            # 2. 상세 정보 스크랩 (Sidecar JSON 저장용 메타데이터 백업)
            meta_data = {
                "doc_number": doc_num,
                "title": title,
                "drafter": doc_item["drafter"],
                "department": doc_item["department"],
                "date": doc_item["date"],
                "doc_type": doc_item["doc_type"],
                "harvested_at": datetime.now().isoformat()
            }
            
            # 3. 첨부파일 선행 다운로드
            attachments = self.download_attachments(detail_page, doc_num)
            
            # 4. 상세 페이지 내 '인쇄' 버튼 찾기 (Robust 다중 셀렉터 통합 및 5초 대기)
            robust_print_selector = "#btnPrint, [title='인쇄'], li#btnPrint, button:has(.print), a:has(.print), .btn_print, img[src*='print'], text=인쇄, button:has-text('인쇄')"
            print_btn = self.find_locator(detail_page, robust_print_selector)
            try:
                print_btn.wait_for(state="visible", timeout=5000)
            except Exception:
                pass
            
            if print_btn.is_visible():
                # 5. 인쇄 버튼 클릭 (force=True 적용) ➡️ '인쇄항목 선택' 모달 레이어 대기
                print_btn.click(force=True)
                print("   [Harvester] Clicked print button. Waiting for selection modal...")
                
                # '인쇄항목 선택' 모달 팝업이 떴는지 확인하고 '모두인쇄' 클릭 조작 (최대 5초 동안 iframe 검색 대기)
                print_frame = None
                for _ in range(20):
                    for f in detail_page.frames:
                        try:
                            if f.locator("#Submit1").count() > 0 or f.locator("text=모두인쇄").count() > 0:
                                print_frame = f
                                break
                        except Exception:
                            pass
                    if print_frame:
                        break
                    time.sleep(0.25)
                
                print_page = None
                if print_frame:
                    # iframe 내부 모두인쇄 Robust 셀렉터 처리
                    modal_all_print = print_frame.locator("#Submit1, text=모두인쇄, button:has-text('모두인쇄')").first
                    try:
                        modal_all_print.wait_for(state="visible", timeout=5000)
                        print("   [Harvester] Found 모두인쇄 inside iframe. Clicking...")
                        with context.expect_page(timeout=15000) as print_popup_info:
                            modal_all_print.click()
                        print_page = print_popup_info.value
                    except Exception as me:
                        print(f"   [Harvester Warning] '모두인쇄' 대기/클릭 실패: {me}. 기본 클릭 시도...")
                        modal_all_print_fb = self.find_locator(detail_page, "text=모두인쇄, button:has-text('모두인쇄')")
                        if modal_all_print_fb.is_visible():
                            with context.expect_page(timeout=15000) as print_popup_info:
                                modal_all_print_fb.click()
                            print_page = print_popup_info.value
                        else:
                            raise me
                else:
                    print("   [Harvester Warning] '인쇄항목 선택' 프레임을 찾지 못했습니다. 기본 클릭 시도...")
                    modal_all_print = self.find_locator(detail_page, "text=모두인쇄, button:has-text('모두인쇄')")
                    if modal_all_print.is_visible():
                        with context.expect_page(timeout=15000) as print_popup_info:
                            modal_all_print.click()
                        print_page = print_popup_info.value
                    else:
                        raise ValueError("모두인쇄 버튼을 찾을 수 없습니다.")
                
                if print_page:
                    print_page.wait_for_load_state("load")
                
                # 6. CDP 직접 PDF 변환 저장
                pdf_path = self.download_pdf_via_cdp(context, print_page, doc_num, title)
                
                # 7. Sidecar 메타데이터 파일 쓰기
                safe_title = self.sanitize_filename(title)
                safe_num = self.sanitize_filename(doc_num) if doc_num else "NO_NUM"
                meta_filename = f"{safe_num}_{safe_title}_meta.json"
                meta_path = self.source_docs_dir / meta_filename
                
                meta_data["attachments_files"] = attachments
                meta_data["pdf_path"] = pdf_path
                meta_path.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
                
                # 리소스 닫기
                try:
                    print_page.close()
                except Exception: pass
                
            else:
                raise ValueError("결재문서 상세 화면에서 '인쇄' 버튼을 감지하지 못했습니다.")
                
            # 상세조회 탭 닫기
            try:
                detail_page.close()
            except Exception: pass
            
            time.sleep(1)
            return True
            
        except Exception as e:
            self.log_error("DETAIL_PROCESSING_FLOW", e, doc_id=doc_num, doc_title=title)
            print(f"🚨 [Harvester Fatal] Failed to harvest document {doc_num}: {e}")
            return False

    def run_harvest(self, mode="daily", years_range=None) -> list:
        """수집 시퀀스 총 지휘 실행"""
        harvested_files = []
        failed_queue = [] # 일시적 수집 실패 건을 최종 시점 재시도하기 위한 큐
        
        with sync_playwright() as p:
            # 크롬 CDP 연결
            browser, context, gw_page = self.connect_browser(p)
            
            try:
                # 1. 로그인 확인
                self.check_login_and_authenticate(gw_page)
                self.check_and_unlock_screen(gw_page)
                
                # 2. 전자결재 시스템 진입
                self.navigate_to_approval_system(gw_page)
                
                # 수집 연도 범위 설정
                if mode == "daily":
                    # 데일리는 최근 1년만 디폴트로 순회
                    years = [None] 
                else:
                    years = years_range if years_range else self.default_years
                    
                print(f"[Harvester] Harvest Mode: {mode.upper()} | Targets: {years}")
                
                # 체크포인트 로드
                checkpoint = self.get_checkpoint()
                
                for year in years:
                    self.check_and_unlock_screen(gw_page)
                    # '결재한문서' 메뉴로 선택 및 연도 드롭다운 변경
                    self.select_menu_and_year(gw_page, "결재한문서", year)
                    time.sleep(3.0) # 연도 변경 후 리스트 테이블 렌더링 대기
                    
                    current_page = 1
                    target_doc_idx = 0
                    consecutive_skips = 0
                    
                    # 체크포인트 이어받기
                    if checkpoint["year"] == year:
                        current_page = checkpoint["page"]
                        target_doc_idx = checkpoint["doc_index"]
                        print(f"[Harvester Stateful Resume] Resuming from Year: {year}, Page: {current_page}, Index: {target_doc_idx}")
                        
                        # 해당 페이지 번호 클릭하여 이동
                        if current_page > 1:
                            success = self.go_to_page(gw_page, current_page)
                            if not success:
                                print(f"[Harvester Warning] Failed to resume to page {current_page}")
                                
                    while True:
                        print(f"\n[Harvester] Scaning Year: {year or 'Default'}, Page: {current_page}")
                        # 3. 목록 파싱
                        queue = self.parse_list_page_and_queue(gw_page)
                        if not queue:
                            print("[Harvester] Document list is empty. Finished this section.")
                            break
                            
                        total_docs = len(queue)
                        
                        # 4. 개별 순회 클릭
                        while target_doc_idx < total_docs:
                            self.check_and_unlock_screen(gw_page)
                            doc_item = queue[target_doc_idx]
                            
                            # 로컬 캐시 대조하여 이미 완료된 건 스킵
                            safe_num = self.sanitize_filename(doc_item["doc_number"]) if doc_item["doc_number"] else "NO_NUM"
                            safe_title = self.sanitize_filename(doc_item["title"])
                            target_pdf_name = f"{safe_num}_{safe_title}.pdf"
                            target_html_name = f"{safe_num}_{safe_title}.html"
                            
                            if (self.source_docs_dir / target_pdf_name).exists() or (self.source_docs_dir / target_html_name).exists():
                                print(f"   [Skip] Already harvested: {doc_item['title']}")
                                target_doc_idx += 1
                                self.save_checkpoint(year, current_page, target_doc_idx)
                                consecutive_skips += 1
                                
                                # 데일리 모드이고 이미 수집한 문서가 연속 20건 이상 나오면 탐색 종료
                                if mode == "daily" and consecutive_skips >= 20:
                                    print("[Harvester Daily Complete] Reached stable historical zone (20 consecutive skips). Stopping.")
                                    self.clear_checkpoint()
                                    return harvested_files
                                continue
                                
                            # 수집 수행
                            success = self.process_single_document(context, gw_page, doc_item)
                            if success:
                                consecutive_skips = 0 # 새 문서 수집 시 카운터 초기화
                                # 수집된 파일 추가
                                harvested_files.append({
                                    "doc_number": doc_item["doc_number"],
                                    "title": doc_item["title"],
                                    "date": doc_item["date"],
                                    "drafter": doc_item["drafter"],
                                    "department": doc_item["department"],
                                    "doc_type": doc_item["doc_type"],
                                    "filename": target_pdf_name if (self.source_docs_dir / target_pdf_name).exists() else target_html_name
                                })
                            else:
                                print(f"   [Harvester Warning] First attempt failed. Adding to failed_queue: {doc_item['title']}")
                                failed_queue.append((current_page, doc_item))
                                
                            target_doc_idx += 1
                            # 성공/실패 여부에 관계없이 진행 상황 세이브 (체크포인트)
                            self.save_checkpoint(year, current_page, target_doc_idx)
                            time.sleep(self.click_delay)

                        # 다음 페이지 이동
                        next_page = current_page + 1
                        success = self.go_to_page(gw_page, next_page)
                        if success:
                            current_page = next_page
                            target_doc_idx = 0 # 새 페이지의 첫 문서부터
                            self.save_checkpoint(year, current_page, target_doc_idx)
                            time.sleep(self.click_delay)
                        else:
                            print(f"[Harvester] No more pages in year {year or 'Default'}. Finished.")
                            break
                    
                    # === Retry Phase (실패 건 재시도) ===
                    if failed_queue:
                        print(f"\n[Harvester Retry Phase] Starting retry for {len(failed_queue)} failed documents...")
                        for retry_page, doc_item in failed_queue:
                            print(f"\n[Harvester Retry] Attempting retry for: {doc_item['title']} on Page {retry_page}")
                            self.check_and_unlock_screen(gw_page)
                            
                            # 해당 페이지로 강제 복귀
                            if self.go_to_page(gw_page, retry_page):
                                time.sleep(1.5)
                                # 재시도 수집 수행
                                success = self.process_single_document(context, gw_page, doc_item)
                                if success:
                                    print(f"   [Harvester Retry Success] Successfully harvested: {doc_item['title']}")
                                    safe_num = self.sanitize_filename(doc_item["doc_number"]) if doc_item["doc_number"] else "NO_NUM"
                                    safe_title = self.sanitize_filename(doc_item["title"])
                                    target_pdf_name = f"{safe_num}_{safe_title}.pdf"
                                    target_html_name = f"{safe_num}_{safe_title}.html"
                                    harvested_files.append({
                                        "doc_number": doc_item["doc_number"],
                                        "title": doc_item["title"],
                                        "date": doc_item["date"],
                                        "drafter": doc_item["drafter"],
                                        "department": doc_item["department"],
                                        "doc_type": doc_item["doc_type"],
                                        "filename": target_pdf_name if (self.source_docs_dir / target_pdf_name).exists() else target_html_name
                                    })
                                else:
                                    print(f"   [Harvester Retry Fail] Failed again for: {doc_item['title']}")
                            else:
                                print(f"   [Harvester Retry Warning] Failed to navigate to page {retry_page} for retry.")
                            
                # 모든 프로세스 완료 후 체크포인트 초기화
                self.clear_checkpoint()
                
            except Exception as e:
                self.log_error("RUN_HARVEST_FLOW", e)
                print(f"[Harvester Fatal Loop Error] {e}")
                
            finally:
                try:
                    browser.close()
                except Exception: pass
                
        return harvested_files

if __name__ == "__main__":
    # 단독 가동 테스트
    try:
        harvester = HarvesterAgent()
        print("Harvester initialized. Starting mock daily harvest...")
        # harvester.run_harvest(mode="daily")
    except Exception as ex:
        print(f"Error: {ex}")
