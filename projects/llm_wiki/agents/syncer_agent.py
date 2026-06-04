import os
import sys
import json
import subprocess
import re
from pathlib import Path
from datetime import datetime
import traceback
import yaml

# 프로젝트 루트 경로
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class SyncerAgent:
    def __init__(self, config_path=None):
        if not config_path:
            config_path = os.path.join(project_root, "config.yaml")
        self.config_path = Path(config_path)
        self.load_config()
        self.setup_logging()
        
    def load_config(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
            
        self.vault_root = Path(self.cfg["paths"]["wiki_vault"])
        self.sync_log_path = Path(os.path.join(os.path.dirname(self.config_path), self.cfg["notebooklm"]["sync_log_path"]))
        self.error_log_path = Path(os.path.join(os.path.dirname(self.config_path), self.cfg["paths"]["error_log"]))
        self.target_notebook_id = self.cfg["notebooklm"]["notebook_id"]
        
        self.sync_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.error_log_path.parent.mkdir(parents=True, exist_ok=True)

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

    def run_nlm_cmd(self, args) -> subprocess.CompletedProcess:
        """nlm CLI 명령어 호출 실행 래퍼 (OS 커맨드 인젝션 차단 및 Windows 호환성 보장)"""
        try:
            # 1. 문자열로 들어온 경우 안전한 인자 리스트로 파싱
            if isinstance(args, str):
                import shlex
                args = shlex.split(args, posix=(os.name != 'nt'))

            # 2. Windows(NT) 환경에서 shell=False 사용 시 .cmd 스크립트 실행을 위한 첫 인자 보정
            if os.name == 'nt' and args and args[0] == 'nlm':
                import shutil
                # 시스템 PATH 상의 nlm.cmd/nlm.bat 전체 경로 획득 시도
                resolved_path = shutil.which('nlm')
                if resolved_path:
                    args[0] = resolved_path
                else:
                    # 탐색 실패 시 윈도우 환경 기본값 폴백
                    args[0] = 'nlm.cmd'

            # 3. shell=False 및 리스트 인자 전달 → 셸 해석 없이 직접 실행 (인젝션 불가)
            res = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding='utf-8',
                shell=False
            )
            return res
        except Exception as e:
            # 예외 발생 시 Mock CompletedProcess 구성
            class DummyResult:
                def __init__(self, error):
                    self.returncode = -1
                    self.stdout = ""
                    self.stderr = str(error)
            return DummyResult(e)

    def get_sync_log(self) -> dict:
        if self.sync_log_path.exists():
            try:
                with open(self.sync_log_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_sync_log(self, sync_log: dict):
        try:
            with open(self.sync_log_path, "w", encoding="utf-8") as f:
                json.dump(sync_log, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Syncer Warning] Cannot save sync log: {e}")

    def verify_auth(self) -> bool:
        """nlm CLI 인증 세션이 살아있는지 검사"""
        res = self.run_nlm_cmd(["nlm", "notebook", "list", "--json"])
        if res.returncode != 0 or "Authentication expired" in res.stderr or "Authentication Error" in res.stderr:
            return False
        return True

    def upload_single_file(self, file_path: str, title: str, notebook_id: str = None) -> bool:
        """단일 마크다운/문서 파일을 NotebookLM에 업로드"""
        nb_id = notebook_id or self.target_notebook_id
        if not nb_id or nb_id == "YOUR_TARGET_NOTEBOOK_ID":
            print("[Syncer Error] Target Notebook ID is not set. Please configure target notebook ID first.")
            return False

        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            print(f"[Syncer Error] File not found for upload: {abs_path}")
            return False

        print(f"[Syncer nlm] Uploading '{title}' to notebook '{nb_id}'...")
        args = ["nlm", "source", "add", nb_id, "--file", abs_path, "--title", title, "--wait"]
        res = self.run_nlm_cmd(args)
        
        if res.returncode == 0:
            print(f"[Syncer nlm Success] Upload completed: {title}")
            return True
        else:
            err_msg = res.stderr or res.stdout
            print(f"[Syncer nlm Error] Upload failed: {err_msg}")
            self.log_error("NLM_UPLOAD", Exception(err_msg), doc_title=title)
            return False

    def build_monthly_consolidated_markdown(self, year_month: str) -> Path:
        """특정 연도-월(예: '2025-05')의 Obsidian Source 마크다운 문서들을 하나로 합친 마크다운 파일을 생성합니다."""
        source_dir = self.vault_root / "Source"
        if not source_dir.exists():
            raise FileNotFoundError("Obsidian Source 폴더가 존재하지 않습니다.")

        # 타겟 월에 해당하는 마크다운 파일 검색
        target_files = []
        for file in source_dir.glob("*.md"):
            try:
                # 파일 내용을 읽어 YAML frontmatter에서 date 날짜 파싱
                content = file.read_text(encoding="utf-8")
                # 간단한 정규식으로 date: "2025-05-19" 추출
                match = re.search(r"date:\s*['\"]?(\d{4}-\d{2})-\d{2}['\"]?", content)
                if match and match.group(1) == year_month:
                    target_files.append(file)
            except Exception:
                pass

        if not target_files:
            print(f"[Syncer Consolidated] No files found for month: {year_month}")
            return None

        # 파일 정렬 (파일명 순)
        target_files.sort(key=lambda x: x.name)

        consolidated_filename = f"{year_month.replace('-', '_')}_기획예산과_결재문서_합본.md"
        consolidated_path = self.vault_root / "Source" / consolidated_filename
        
        print(f"[Consolidated Builder] Merging {len(target_files)} documents into: {consolidated_filename}")
        
        merged_lines = [
            "---",
            f"title: \"{year_month.replace('-', '년 ')}월 기획예산과 결재문서 합본\"",
            f"compiled_at: \"{datetime.now().isoformat()}\"",
            "type: \"합본\"",
            "---",
            "",
            f"# 📚 {year_month.replace('-', '년 ')}월 기획예산과 결재문서 합본",
            f"\n> 본 합본 문서는 NotebookLM 적재 최적화(소스 수 제한 우회)를 위해 자동 생성되었습니다.",
            f"> 포함된 개별 결재 건수: 총 {len(target_files)}건\n",
            "---"
        ]

        for file in target_files:
            # 본문에 앞서 헤더 구분선 삽입
            doc_name = file.stem
            merged_lines.append(f"\n\n## 📄 결재원장: [[Source/{doc_name}]]")
            merged_lines.append("\n" + "="*40 + "\n")
            
            # 본문 병합 (Frontmatter 영역은 제외하고 본문만 병합)
            content = file.read_text(encoding="utf-8")
            parts = content.split("---", 2)
            body_text = parts[2].strip() if len(parts) >= 3 else content
            merged_lines.append(body_text)
            merged_lines.append("\n\n---\n")

        consolidated_path.write_text("\n".join(merged_lines), encoding="utf-8")
        print(f"[Consolidated Builder Success] Consolidated file created at: {consolidated_path.name}")
        return consolidated_path

    def sync_daily_changes(self, new_harvester_files: list = None) -> int:
        """일일 동기화(Track 2) 수집된 개별 신규 결재건들을 NotebookLM에 실시간 개별 업로드.
        만약 new_harvester_files가 제공되지 않거나 비어 있다면, Source/ 폴더를 전체 검색하여
        동기화 로그에 없는 마크다운 파일을 찾아 업로드합니다."""
        if not self.verify_auth():
            print("[Syncer Error] NotebookLM auth is expired. Cannot sync daily updates.")
            return 0

        sync_log = self.get_sync_log()
        success_count = 0

        # 만약 new_harvester_files가 비어 있다면 Source 폴더 내 실제 파일들 중 미동기화 건을 업로드 후보로 선정
        if not new_harvester_files:
            new_harvester_files = []
            source_dir = self.vault_root / "Source"
            if source_dir.exists():
                for md_file in source_dir.glob("*.md"):
                    # 합본 파일이나 기존 예시 파일은 업로드 대상에서 제외하고, 결재 문서 형태만 타겟팅
                    if "합본" in md_file.name or md_file.name in ["2023_OO도로건설_결산보고서.md", "2024_OO도로건설_예산요구서.md", "2024_긴축재정지침.md", "기획예산과-8638_temp_test_doc.md"]:
                        continue
                    
                    # 파일명 stem을 키로 매칭
                    if md_file.stem not in sync_log:
                        # 파일 내용에서 제목 추출 시도
                        title = md_file.stem
                        try:
                            content = md_file.read_text(encoding="utf-8")
                            match = re.search(r'title:\s*"(.*?)"', content)
                            if match:
                                title = match.group(1)
                        except Exception:
                            pass
                            
                        # 문서 정보 유추
                        doc_num = md_file.name.split("_")[0] if "_" in md_file.name else ""
                        new_harvester_files.append({
                            "doc_number": doc_num,
                            "title": title,
                            "stem_name": md_file.stem
                        })

        for doc in new_harvester_files:
            stem_name = doc.get("stem_name")
            if not stem_name:
                doc_num = doc["doc_number"]
                safe_num = re.sub(r'[\\/:*?"<>|,]', '_', doc_num) if doc_num else "NO_NUM"
                safe_title = re.sub(r'[\\/:*?"<>|,]', '_', doc["title"])
                stem_name = f"{safe_num}_{safe_title}"
                if len(stem_name) > 80:
                    stem_name = stem_name[:80]
                    
            md_path = self.vault_root / "Source" / f"{stem_name}.md"
            
            # 이미 업로드된 이력이 있거나 마크다운 파일이 없으면 스킵
            if stem_name in sync_log or not md_path.exists():
                continue

            # 개별 업로드 실행
            success = self.upload_single_file(str(md_path), f"결재: {doc['title']}", self.target_notebook_id)
            if success:
                sync_log[stem_name] = {
                    "synced_at": datetime.now().isoformat(),
                    "method": "daily_individual",
                    "notebook_id": self.target_notebook_id
                }
                success_count += 1
                self.save_sync_log(sync_log)

        return success_count

    def sync_historical_migration(self, years_range: list) -> int:
        """과거 데이터 일괄 적재(Track 1) 시, 월 단위로 마크다운을 자동 합치고 합본 단위로 NotebookLM에 업로드"""
        if not self.verify_auth():
            print("[Syncer Error] NotebookLM auth is expired. Cannot sync historical migration.")
            return 0

        sync_log = self.get_sync_log()
        success_count = 0

        # 지정 연도의 각 월별 리스트 작성 (예: 2025-01, 2025-02 ...)
        target_months = []
        for year in years_range:
            for month in range(1, 13):
                target_months.append(f"{year}-{month:02d}")

        print(f"[Syncer Track 1] Starting monthly consolidation for target months: {target_months}")

        for year_month in target_months:
            log_key = f"consolidated_{year_month}"
            # 이미 이번 달 합본이 업로드된 상태면 건너뜀
            if log_key in sync_log:
                print(f"[Syncer Skip] Consolidated month {year_month} is already synced.")
                continue

            # 1. 월별 합본 마크다운 생성
            consolidated_file = self.build_monthly_consolidated_markdown(year_month)
            if not consolidated_file:
                continue # 파일이 없으면 스킵

            # 2. 합본 파일 업로드 실행
            title = f"{year_month.replace('-', '년 ')}월 기획예산과 결재문서 합본"
            success = self.upload_single_file(str(consolidated_file), title, self.target_notebook_id)
            
            if success:
                # 3. 합본 내에 포함된 개별 문서들을 캐시 로그에 묶어 저장
                sync_log[log_key] = {
                    "synced_at": datetime.now().isoformat(),
                    "method": "consolidated_monthly",
                    "filename": consolidated_file.name,
                    "notebook_id": self.target_notebook_id
                }
                
                # 합본에 포함된 개별 문서들도 중복 방지를 위해 로그에 마크
                source_dir = self.vault_root / "Source"
                for file in source_dir.glob("*.md"):
                    try:
                        content = file.read_text(encoding="utf-8")
                        match = re.search(r"date:\s*['\"]?(\d{4}-\d{2})-\d{2}['\"]?", content)
                        if match and match.group(1) == year_month and file.stem != consolidated_file.stem:
                            sync_log[file.stem] = {
                                "synced_at": datetime.now().isoformat(),
                                "method": f"merged_in_{consolidated_file.stem}",
                                "notebook_id": self.target_notebook_id
                            }
                    except Exception:
                        pass
                
                self.save_sync_log(sync_log)
                success_count += 1

        return success_count

if __name__ == "__main__":
    # 단독 테스트
    try:
        syncer = SyncerAgent()
        print("Syncer Agent Initialized successfully.")
        # syncer.sync_historical_migration([2025])
    except Exception as ex:
        print(f"Error: {ex}")
