"""网盘客户端与路由的单测。

重点固化「路径不存在 ≠ 错误」的语义（多网盘方案允许文件只存在于一个网盘）：
- 夸克 CLI 返回 code=FILE_NOT_FOUND、百度 BaiduPCS-Go 返回 代码: -9 时，
  客户端必须抛 PanPathNotFoundError 而不是普通 PanError；
- 其他错误（登录失效、其他错误码）仍必须是普通 PanError，不得被误判；
- /pan/entries 路由把 PanPathNotFoundError 当作「该层为空」（ok=True + missing），
  真实失败仍标 ok=False。

运行：cd backend-web && .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.api.routes.pan_browser as pan_browser
from app.services.pan.base import PanError, PanPathNotFoundError, merge_key, merge_pan_entries
from app.services.pan.baidu_pan_client import BaiduPanClient
from app.services.pan.quark_pan_client import QuarkPanClient

# 线上真实出现过的报错文本，作为识别样本
_BAIDU_REAL_NOT_FOUND = (
    "获取目录下的文件列表: 遇到错误, 远端服务器返回错误, 代码: -9, 消息: 文件不存在"
)


def _make_quark(monkeypatch: pytest.MonkeyPatch, tmp_path, stdout: str, stderr: str = "") -> QuarkPanClient:
    (tmp_path / "kuake").write_text("")
    monkeypatch.setenv("KUAKE_BIN", str(tmp_path / "kuake"))
    monkeypatch.setenv("KUAKE_WORKDIR", str(tmp_path))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=stderr)
    )
    return QuarkPanClient()


def _make_baidu(monkeypatch: pytest.MonkeyPatch, tmp_path, stdout: str, stderr: str = "") -> BaiduPanClient:
    (tmp_path / "BaiduPCS-Go").write_text("")
    monkeypatch.setenv("BAIDUPCS_BIN", str(tmp_path / "BaiduPCS-Go"))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=stderr)
    )
    return BaiduPanClient()


def _quark_stdout(code: str, message: str) -> str:
    return json.dumps({"success": False, "code": code, "message": message}, ensure_ascii=False)


# ==================== 夸克客户端 ====================

class TestQuarkPanClient:
    def test_list_not_found_raises_path_not_found(self, monkeypatch, tmp_path):
        client = _make_quark(
            monkeypatch, tmp_path,
            _quark_stdout("FILE_NOT_FOUND", "failed to get directory info: file not found: /资料池/abc"),
        )
        with pytest.raises(PanPathNotFoundError):
            client.list_entries("/资料池/abc")

    def test_other_error_still_pan_error(self, monkeypatch, tmp_path):
        client = _make_quark(monkeypatch, tmp_path, _quark_stdout("AUTH_EXPIRED", "登录失效"))
        with pytest.raises(PanError) as exc_info:
            client.list_entries("/")
        assert not isinstance(exc_info.value, PanPathNotFoundError)

    def test_success_parses_entries(self, monkeypatch, tmp_path):
        payload = {
            "success": True,
            "code": "OK",
            "data": {
                "list": [
                    {"file_name": "试卷.pdf", "path": "/资料池/试卷.pdf", "dir": False,
                     "fid": 123, "size": 1024, "mtime": 1700000000},
                    {"file_name": "高一", "path": "/资料池/高一", "dir": True,
                     "fid": 456, "size": 0, "mtime": 1700000001},
                ]
            },
        }
        client = _make_quark(monkeypatch, tmp_path, json.dumps(payload, ensure_ascii=False))
        entries = client.list_entries("/资料池")
        assert len(entries) == 2
        assert entries[0]["name"] == "试卷.pdf"
        assert entries[0]["is_dir"] is False
        assert entries[0]["fid"] == "123"
        assert entries[1]["is_dir"] is True


# ==================== 百度客户端 ====================

class TestBaiduPanClient:
    def test_minus9_raises_path_not_found(self, monkeypatch, tmp_path):
        client = _make_baidu(monkeypatch, tmp_path, stdout=_BAIDU_REAL_NOT_FOUND)
        with pytest.raises(PanPathNotFoundError):
            client.list_entries("/资料池/某文件夹")

    def test_minus9_without_space_also_detected(self, monkeypatch, tmp_path):
        client = _make_baidu(monkeypatch, tmp_path, stdout="遇到错误, 远端服务器返回错误, 代码:-9, 消息: 文件不存在")
        with pytest.raises(PanPathNotFoundError):
            client.list_entries("/x")

    @pytest.mark.parametrize("text", [
        "遇到错误, 远端服务器返回错误, 代码: -90, 消息: 验证码错误",   # 含 -9 前缀的其他错误码
        "遇到错误, 远端服务器返回错误, 代码: -19, 消息: 其他错误",
        "遇到错误, 远端服务器返回错误, 代码: -5, 消息: 操作过于频繁",
    ])
    def test_similar_error_codes_not_misclassified(self, monkeypatch, tmp_path, text):
        client = _make_baidu(monkeypatch, tmp_path, stdout=text)
        with pytest.raises(PanError) as exc_info:
            client.list_entries("/x")
        assert not isinstance(exc_info.value, PanPathNotFoundError)

    def test_login_failure_still_pan_error(self, monkeypatch, tmp_path):
        client = _make_baidu(monkeypatch, tmp_path, stdout="检测到登录失败, 请尝试重新登录")
        with pytest.raises(PanError) as exc_info:
            client.status_check()
        assert not isinstance(exc_info.value, PanPathNotFoundError)

    def test_ls_parses_entries(self, monkeypatch, tmp_path):
        out = (
            "  0    17518712  2026-09-30 10:00:00  数学试卷.pdf\n"
            "  1         -  2026-10-01 12:00:00  高一/\n"
        )
        client = _make_baidu(monkeypatch, tmp_path, stdout=out)
        entries = client.list_entries("/资料池")
        assert len(entries) == 2
        assert entries[0]["name"] == "数学试卷.pdf"
        assert entries[0]["is_dir"] is False
        assert entries[0]["size"] == 17518712
        assert entries[1]["name"] == "高一"
        assert entries[1]["is_dir"] is True


# ==================== 合并逻辑（纯函数） ====================

class TestMergePanEntries:
    def test_same_name_in_two_pans_merges_to_one_row(self):
        per_pan = {
            "quark": [{"name": "试卷.pdf", "path": "/a/试卷.pdf", "is_dir": False,
                       "fid": "1", "size": 100, "mtime": 1}],
            "baidu": [{"name": "试卷.pdf", "path": "/a/试卷.pdf", "is_dir": False,
                       "fid": None, "size": 100, "mtime": 1}],
        }
        merged = merge_pan_entries(per_pan)
        assert len(merged) == 1
        assert merged[0]["file_name"] == "试卷.pdf"
        assert {l["pan_type"] for l in merged[0]["locations"]} == {"quark", "baidu"}

    def test_missing_pan_contributes_nothing(self):
        # 文件只存在于夸克：百度这一层为空（路径不存在按空列表处理后的形态）
        per_pan = {
            "quark": [{"name": "试卷.pdf", "path": "/a/试卷.pdf", "is_dir": False,
                       "fid": "1", "size": 100, "mtime": 1}],
            "baidu": [],
        }
        merged = merge_pan_entries(per_pan)
        assert len(merged) == 1
        assert [l["pan_type"] for l in merged[0]["locations"]] == ["quark"]

    def test_merge_key_case_insensitive(self):
        assert merge_key("AbC.PDF") == merge_key("abc.pdf")


# ==================== /pan/entries 路由 ====================

class _FakeUser:
    id = 1


class _FakeSession:
    pass


class _FakePan:
    """只实现 list_entries 的假客户端。"""

    def __init__(self, entries=None, exc: Exception | None = None):
        self._entries = entries or []
        self._exc = exc

    def list_entries(self, remote: str = "/"):
        if self._exc:
            raise self._exc
        return self._entries


async def _noop_find_pool(*args, **kwargs):
    return None


def _run_entries(monkeypatch, pans: dict[str, _FakePan], path: str = "/资料池"):
    monkeypatch.setattr(pan_browser, "enabled_pan_types", lambda: list(pans))
    monkeypatch.setattr(pan_browser, "make_client", lambda t: pans[t])
    monkeypatch.setattr(pan_browser, "_find_pool_by_pan_path", _noop_find_pool)
    return asyncio.run(
        pan_browser.pan_entries(path=path, current_user=_FakeUser(), session=_FakeSession())
    )


_QUARK_ENTRY = {"name": "试卷.pdf", "path": "/资料池/试卷.pdf", "is_dir": False,
                "fid": "1", "size": 100, "mtime": 1}


class TestPanEntriesRoute:
    def test_not_found_pan_treated_as_empty_and_ok(self, monkeypatch):
        res = _run_entries(monkeypatch, {
            "quark": _FakePan(entries=[_QUARK_ENTRY]),
            "baidu": _FakePan(exc=PanPathNotFoundError("BaiduPCS-Go 路径不存在：代码: -9")),
        })
        assert res.success is True
        drives = {d["pan_type"]: d for d in res.data["drives"]}
        assert drives["quark"]["ok"] is True
        assert drives["baidu"]["ok"] is True          # 路径不存在不标失败
        assert drives["baidu"]["missing"] is True      # 但带 missing 标记
        assert "error" not in drives["baidu"]
        # 列表只含夸克一侧的文件
        assert [r["file_name"] for r in res.data["list"]] == ["试卷.pdf"]
        assert [l["pan_type"] for l in res.data["list"][0]["locations"]] == ["quark"]

    def test_real_failure_still_flagged(self, monkeypatch):
        res = _run_entries(monkeypatch, {
            "quark": _FakePan(entries=[_QUARK_ENTRY]),
            "baidu": _FakePan(exc=PanError("请尝试重新登录")),
        })
        assert res.success is True
        drives = {d["pan_type"]: d for d in res.data["drives"]}
        assert drives["baidu"]["ok"] is False
        assert "请尝试重新登录" in drives["baidu"]["error"]
        assert "missing" not in drives["baidu"]

    def test_all_pans_failed_returns_error(self, monkeypatch):
        res = _run_entries(monkeypatch, {
            "quark": _FakePan(exc=PanError("kuake 超时")),
            "baidu": _FakePan(exc=PanError("登录失效")),
        })
        assert res.success is False
        assert res.data is None

    def test_both_pans_not_found_shows_empty_not_error(self, monkeypatch):
        res = _run_entries(monkeypatch, {
            "quark": _FakePan(exc=PanPathNotFoundError("file not found")),
            "baidu": _FakePan(exc=PanPathNotFoundError("代码: -9")),
        })
        assert res.success is True
        assert res.data["list"] == []


# ==================== 网盘根目录（PAN_ROOT_DIR） ====================

class TestPanRootDir:
    def test_default_root_is_xianyu(self, monkeypatch):
        monkeypatch.delenv("PAN_ROOT_DIR", raising=False)
        assert pan_browser._pan_root_dir() == "/闲鱼"

    def test_root_env_override_and_normalize(self, monkeypatch):
        monkeypatch.setenv("PAN_ROOT_DIR", "资料池/")   # 自动补前导 /、去尾 /
        assert pan_browser._pan_root_dir() == "/资料池"
        monkeypatch.setenv("PAN_ROOT_DIR", "/")
        assert pan_browser._pan_root_dir() == "/"

    def test_resolve_root_alias_and_clamp(self):
        root = "/闲鱼"
        assert pan_browser._resolve_pan_path("", root) == root
        assert pan_browser._resolve_pan_path("/", root) == root
        assert pan_browser._resolve_pan_path("/闲鱼", root) == "/闲鱼"
        assert pan_browser._resolve_pan_path("/闲鱼/资料池", root) == "/闲鱼/资料池"
        # 越出根目录的路径收拢回根
        assert pan_browser._resolve_pan_path("/资料池", root) == root
        assert pan_browser._resolve_pan_path("/闲鱼/../资料池", root) == root
        # 同名前缀防误伤：/闲鱼资料 不在 /闲鱼 之内
        assert pan_browser._resolve_pan_path("/闲鱼资料/x", root) == root

    def test_resolve_with_root_slash(self):
        # 根为 / 时退化为纯规范化，兼容旧行为
        assert pan_browser._resolve_pan_path("/", "/") == "/"
        assert pan_browser._resolve_pan_path("/资料池", "/") == "/资料池"
        assert pan_browser._resolve_pan_path("/a/../资料池", "/") == "/资料池"

    def test_entries_defaults_to_root_and_returns_it(self, monkeypatch):
        monkeypatch.setenv("PAN_ROOT_DIR", "/闲鱼")
        seen: list[str] = []

        class _RecordingPan:
            def list_entries(self, remote: str = "/"):
                seen.append(remote)
                return [_QUARK_ENTRY]

        res = _run_entries(monkeypatch, {"quark": _RecordingPan()}, path="/")
        assert res.success is True
        assert seen == ["/闲鱼"]            # 「/」别名解析为根目录后查询
        assert res.data["root"] == "/闲鱼"   # 响应带 root 供前端做面包屑起点

    def test_entries_clamps_out_of_root_path(self, monkeypatch):
        monkeypatch.setenv("PAN_ROOT_DIR", "/闲鱼")
        seen: list[str] = []

        class _RecordingPan:
            def list_entries(self, remote: str = "/"):
                seen.append(remote)
                return [_QUARK_ENTRY]

        res = _run_entries(monkeypatch, {"quark": _RecordingPan()}, path="/资料池")
        assert res.success is True
        assert seen == ["/闲鱼"]
        assert res.data["path"] == "/闲鱼"


# ==================== 网盘互传（同步） ====================

class TestBaiduSyncCommands:
    """百度客户端 download/upload/delete 的输出解析（实测输出样本固化）。"""

    def test_download_parses_saved_path(self, monkeypatch, tmp_path):
        saved = tmp_path / "1157652378_榕树下的榕树2"
        saved.mkdir(parents=True)
        (saved / "多盘合并测试.txt").write_text("x")
        out = (
            "\n[1] ↓ 39B/39B 49B/s in 1s, left 0s ............\n"
            f"[1] 下载完成, 保存位置: {saved / '多盘合并测试.txt'}\n"
            "[1] 跳过文件有效性检验\n\n下载结束, 时间: 1.858s, 数据总量: 39B\n"
        )
        client = _make_baidu(monkeypatch, tmp_path, stdout=out)
        assert client.download("/闲鱼/多盘合并测试.txt", str(tmp_path)) == str(
            saved / "多盘合并测试.txt"
        )

    def test_download_without_saved_path_raises(self, monkeypatch, tmp_path):
        client = _make_baidu(monkeypatch, tmp_path, stdout="下载结束, 时间: 1s")
        with pytest.raises(PanError, match="保存位置"):
            client.download("/a.txt", str(tmp_path))

    def test_upload_parses_remote_path(self, monkeypatch, tmp_path):
        local = tmp_path / "upload_test.txt"
        local.write_text("hello")
        out = "\n[1] 上传文件成功, 保存到网盘路径: /闲鱼/_synctest/upload_test.txt\n\n上传结束, 时间: 2.489s, 总大小: 16B\n"
        client = _make_baidu(monkeypatch, tmp_path, stdout=out)
        assert client.upload(str(local), "/闲鱼/_synctest") == "/闲鱼/_synctest/upload_test.txt"

    def test_upload_missing_local_raises(self, monkeypatch, tmp_path):
        client = _make_baidu(monkeypatch, tmp_path, stdout="")
        with pytest.raises(PanError, match="本地文件不存在"):
            client.upload(str(tmp_path / "nope.txt"), "/闲鱼")

    def test_delete_ok_output(self, monkeypatch, tmp_path):
        out = "操作成功, 以下文件/目录已删除, 可在网盘文件回收站找回: \n  #  文件/目录\n  0  /闲鱼/a.txt\n"
        client = _make_baidu(monkeypatch, tmp_path, stdout=out)
        client.delete("/闲鱼/a.txt")  # 不抛即通过

    def test_ensure_dir_swallows_exists(self, monkeypatch, tmp_path):
        # mkdir 对已存在目录报「遇到错误」时 ensure_dir 必须吞掉继续
        calls: list[list[str]] = []

        def _fake_run(cmd, **k):
            calls.append(cmd)
            if cmd[1] == "ls":
                return SimpleNamespace(stdout="表头\n", stderr="")
            return SimpleNamespace(stdout="遇到错误, 远端服务器返回错误, 代码: -10, 消息: 目录已存在", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        (tmp_path / "BaiduPCS-Go").write_text("")
        monkeypatch.setenv("BAIDUPCS_BIN", str(tmp_path / "BaiduPCS-Go"))
        client = BaiduPanClient()
        client.ensure_dir("/闲鱼/资料池/2026")
        mkdir_calls = [c for c in calls if c[1] == "mkdir"]
        assert [c[2] for c in mkdir_calls] == ["/闲鱼", "/闲鱼/资料池", "/闲鱼/资料池/2026"]


class TestSyncSizeMatch:
    """同步查重的大小比对：小文件必须严格，大文件带 0.5% 容差（覆盖百度 ls 舍入误差）。"""

    def test_exact_match(self):
        assert _sync._sizes_match(1024, 1024) is True

    def test_tiny_files_strict(self):
        # 回归：24B 与 87B 绝不能被视为同一份（旧实现 1024B 下限曾导致错误跳过）
        assert _sync._sizes_match(24, 87) is False
        assert _sync._sizes_match(39, 40) is True  # 1B 差 = 人类可读舍入，视为同一份

    def test_large_files_tolerance(self):
        # 百度 ls 两位小数：24.26MB 舍入误差约 5KB，0.5% 容差足够覆盖
        assert _sync._sizes_match(24 * 1024**2, 24 * 1024**2 + 50 * 1024) is True
        assert _sync._sizes_match(24 * 1024**2, 26 * 1024**2) is False

    def test_unknown_size_never_matches(self):
        assert _sync._sizes_match(None, 100) is False
        assert _sync._sizes_match(100, None) is False


from app.services.pan import pan_sync_service as _sync  # noqa: E402  (放后面避免影响上文阅读)


class TestPanSyncService:
    """同步主流程：假客户端验证 跳过/覆盖/上传/逐文件清理/暂存目录回收。"""

    def _run(self, monkeypatch, tmp_path, src_files, dst_files):
        """src_files: {绝对路径: size}；dst_files: {绝对路径: size}。返回 (任务终态, 删除列表, 上传列表)。"""
        import posixpath

        deleted_dst: list[str] = []
        uploaded_dst: list[str] = []

        class _FakePan:
            def __init__(self, files):
                self._files = files

            def list_entries(self, remote: str = "/"):
                remote = remote.rstrip("/") or "/"
                out = []
                seen_dirs: set[str] = set()
                for path, size in self._files.items():
                    parent = posixpath.dirname(path)
                    # 沿途目录也作为条目出现（真实网盘 list 目录/文件混合）
                    d = parent
                    while d and d != "/":
                        if posixpath.dirname(d) == remote and d not in seen_dirs:
                            seen_dirs.add(d)
                            out.append({
                                "name": posixpath.basename(d),
                                "path": d,
                                "is_dir": True,
                                "fid": None,
                                "size": None,
                                "mtime": None,
                            })
                        d = posixpath.dirname(d)
                    if parent == remote:
                        out.append({
                            "name": posixpath.basename(path),
                            "path": path,
                            "is_dir": False,
                            "fid": None,
                            "size": size,
                            "mtime": None,
                        })
                return out

            def ensure_dir(self, remote: str) -> None:
                return None

            def delete(self, remote: str) -> None:
                deleted_dst.append(remote)

            def upload(self, local_path, remote, timeout=None):
                uploaded_dst.append(str(remote))
                return remote

            def download(self, remote, dest, timeout=None):
                # 模拟百度客户端语义：dest 为目录时，文件落在 dest/ basename(remote)
                p = Path(dest)
                if p.is_dir():
                    p = p / posixpath.basename(remote)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
                return str(p)

        src = _FakePan(src_files)
        dst = _FakePan(dst_files)
        monkeypatch.setattr(_sync, "make_client", lambda t: src if t == "baidu" else dst)
        monkeypatch.setenv("PAN_SYNC_STAGING_DIR", str(tmp_path / "staging"))
        # 落库钩子在 _run_sync_safely 的 finally 中执行，单测不碰真实数据库
        persisted: list[dict] = []

        async def _fake_persist(task: dict) -> None:
            persisted.append(task)

        monkeypatch.setattr(_sync, "_persist_task_log", _fake_persist)

        async def _go():
            task = _sync.start_sync("baidu", "/闲鱼/多盘测试", is_dir=True)
            # start_sync 返回的是不含内部句柄的公开视图，句柄从注册表取
            await _sync._sync_tasks[task["task_id"]]["_handle"]
            return _sync.get_task(task["task_id"])

        task = asyncio.run(_go())
        assert len(persisted) == 1 and persisted[0]["task_id"] == task["task_id"]
        return task, deleted_dst, uploaded_dst

    def test_skip_upload_and_cleanup(self, monkeypatch, tmp_path):
        src = {"/闲鱼/多盘测试/已存在.txt": 100, "/闲鱼/多盘测试/子目录/新文件.txt": 200}
        dst = {"/闲鱼/多盘测试/已存在.txt": 100}
        task, deleted, uploaded = self._run(monkeypatch, tmp_path, src, dst)
        assert task["status"] == "done"
        assert task["total"] == 2
        assert task["uploaded"] == 1 and task["skipped"] == 1 and task["failed"] == 0
        assert deleted == []  # 大小一致不触发覆盖删除
        assert len(uploaded) == 1
        # 逐文件清理 + 任务级 finally 回收：暂存目录应已不存在
        assert not (tmp_path / "staging").exists() or not any((tmp_path / "staging").iterdir())

    def test_overwrite_deletes_dst_first(self, monkeypatch, tmp_path):
        src = {"/闲鱼/多盘测试/旧版本.txt": 87}
        dst = {"/闲鱼/多盘测试/旧版本.txt": 24}
        task, deleted, uploaded = self._run(monkeypatch, tmp_path, src, dst)
        assert task["status"] == "done"
        assert task["uploaded"] == 1 and task["skipped"] == 0
        assert deleted == ["/闲鱼/多盘测试/旧版本.txt"]
        assert len(uploaded) == 1

    def test_single_file_under_dir_walk(self, monkeypatch, tmp_path):
        # 单文件放在子目录里，验证递归收集（_run 固定按文件夹同步）
        src = {"/闲鱼/多盘测试/子目录/单文件.txt": 50}
        task, deleted, uploaded = self._run(monkeypatch, tmp_path, src, {})
        assert task["status"] == "done" and task["total"] == 1 and task["uploaded"] == 1

    def test_reject_unsupported_src(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PAN_SYNC_STAGING_DIR", str(tmp_path / "staging"))
        with pytest.raises(PanError, match="不支持的同步源网盘"):
            _sync.start_sync("aliyun", "/x", is_dir=False)
