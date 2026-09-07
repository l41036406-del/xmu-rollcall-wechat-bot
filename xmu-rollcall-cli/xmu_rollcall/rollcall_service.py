from __future__ import annotations

import io
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from xmulogin import xmulogin

from .config import CONFIG_DIR, ensure_config_dir, get_session_cache_path
from .utils import load_session, save_session, verify_session

BASE_URL = "https://lnt.xmu.edu.cn"
PROFILE_URL = f"{BASE_URL}/api/profile"
ROLLCALLS_URL = f"{BASE_URL}/api/radar/rollcalls"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://ids.xmu.edu.cn/authserver/login",
}
CHINA_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class ValidationResult:
    session: requests.Session
    profile: Dict[str, Any]
    name: str


@dataclass
class RollcallRecord:
    course_title: str
    created_by_name: str
    department_name: str
    is_expired: bool
    is_number: bool
    is_radar: bool
    rollcall_id: int
    rollcall_status: str
    scored: bool
    status: str
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "RollcallRecord":
        return cls(
            course_title=payload.get("course_title", ""),
            created_by_name=payload.get("created_by_name", ""),
            department_name=payload.get("department_name", ""),
            is_expired=bool(payload.get("is_expired", False)),
            is_number=bool(payload.get("is_number", False)),
            is_radar=bool(payload.get("is_radar", False)),
            rollcall_id=int(payload.get("rollcall_id", 0)),
            rollcall_status=payload.get("rollcall_status", ""),
            scored=bool(payload.get("scored", False)),
            status=payload.get("status", ""),
            raw=payload,
        )

    @property
    def type_label(self) -> str:
        if self.is_radar:
            return "雷达签到"
        if self.is_number:
            return "数字签到"
        return "二维码签到"


@dataclass
class AnswerOutcome:
    rollcall: RollcallRecord
    action: str
    success: bool
    message: str
    number_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    response_status: Optional[int] = None
    raw_data: Optional[Dict[str, Any]] = None


@dataclass
class AnswerBatchResult:
    account: Dict[str, Any]
    queried_at: datetime
    rollcalls: List[RollcallRecord]
    outcomes: List[AnswerOutcome]


def find_number_code(data: Any, depth: int = 0, max_depth: int = 10) -> Optional[str]:
    if depth > max_depth:
        return None
    if isinstance(data, dict):
        number_code = data.get("number_code")
        if number_code is not None:
            return str(number_code)
        for value in data.values():
            nested_code = find_number_code(value, depth + 1, max_depth)
            if nested_code:
                return nested_code
    elif isinstance(data, list):
        for item in data:
            nested_code = find_number_code(item, depth + 1, max_depth)
            if nested_code:
                return nested_code
    return None


def _safe_json(response: requests.Response) -> Dict[str, Any]:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"data": payload}
    except ValueError:
        text = (response.text or "").strip()
        return {"text": text[:500]} if text else {}


# ---- 二维码内容解析 ----
#
# 畅课/TronClass 的“二维码点名”在学生端扫码时，会得到下面两种内容之一：
#   1) JSON：{"courseId":..., "data":"...", "rollcallId":...}
#   2) URL： 形如 https://c-mobile.xmu.edu.cn/j?p=0~%102eqk!3~xxxx!4~%108rgx
# 其中 p 是一个按“索引~值”分段的紧凑编码。索引与字段名的对应关系、以及各值的
# 编码规则，是通过逆向 c-mobile 前端 app.js（模块 84272）得到的：
#   - 字段索引按字段名列表的下标 base36 编码：0..9,a(10)
#   - 数值：值以控制符 \x10 开头，其后为 base36 编码；形如 A.B 的为浮点
#   - 布尔/枚举：值以控制符 \x1a 开头，\x1a1=true、\x1a0=false
#   - 字符串里若含原始分隔符 ~ 或 !，会被替换成 \x1f 或 \x1e
_QR_FIELDS = [
    "courseId", "activityId", "activityType", "data", "rollcallId",
    "groupSetId", "accessCode", "action", "enableGroupRollcall",
    "createUser", "joinCourse",
]
_QR_INDEX_FIELD = {str(i): name for i, name in enumerate(_QR_FIELDS)}
# 兼容 index 的 base36 写法（10 -> "a"）
_QR_INDEX_FIELD.update({format(i, "x") if i >= 10 else str(i): name
                        for i, name in enumerate(_QR_FIELDS)})
_QR_NUM_MARK = "\x10"          # 数值前缀
_QR_BOOL_MARK = "\x1a"         # 布尔/枚举前缀
_QR_BOOL_TRUE = "\x1a1"
_QR_BOOL_FALSE = "\x1a0"
_QR_ENUM_NAMES = {
    chr(26) + format(i + 2, "x"): name
    for i, name in enumerate(["classroom-exam", "feedback", "vote"])
}
_QR_ESC_TILDE = "\x1f"         # 编码前的 "~"
_QR_ESC_BANG = "\x1e"          # 编码前的 "!"


def _decode_qr_value(value: str) -> Any:
    if value.startswith(_QR_BOOL_MARK):
        if value == _QR_BOOL_TRUE:
            return True
        if value == _QR_BOOL_FALSE:
            return False
        return _QR_ENUM_NAMES.get(value, value)
    if value.startswith(_QR_NUM_MARK):
        digits = value[1:].split(".")
        numbers = []
        for part in digits:
            if not part:
                continue
            try:
                numbers.append(int(part, 36))
            except ValueError:
                pass
        if not numbers:
            return 0
        if len(numbers) == 1:
            return numbers[0]
        return float(f"{numbers[0]}.{numbers[1]}")
    return value.replace(_QR_ESC_TILDE, "~").replace(_QR_ESC_BANG, "!")


def decode_qr_p_token(p_value: str) -> Dict[str, Any]:
    """把 /j?p=... 的 p 值反序列化成字段字典。

    入参应是 URL 解码后的字符串（控制符已还原）。例如图片扫码得到的
    `p=0~%102eqk!3~...!4~%108rgx`，先经 parse_qs 自动解码后直接传入即可。
    """
    result: Dict[str, Any] = {}
    if not p_value or not isinstance(p_value, str):
        return result
    for segment in p_value.split("!"):
        if not segment:
            continue
        index, sep, value = segment.partition("~")
        if not sep:
            continue
        field_name = _QR_INDEX_FIELD.get(index, index)
        result[field_name] = _decode_qr_value(value)
    return result


def parse_qr_content(content: str) -> Optional[Dict[str, Any]]:
    """解析扫码得到的原始文本，返回签到相关字段（courseId/data/rollcallId 等）。

    支持 JSON 与 c-mobile 跳转 URL 两种格式；解析不出返回 None。
    """
    if not content or not isinstance(content, str):
        return None
    text = content.strip()
    if not text:
        return None

    # 1) 直接是 JSON
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    # 2) URL 形式：c-mobile /j 或 /scanner-jumper，携带 p 或 _p
    if "?" in text:
        try:
            query = urlparse(text).query
        except ValueError:
            query = text.split("?", 1)[1]
        params = parse_qs(query)
        if "_p" in params and params["_p"]:
            try:
                payload = json.loads(params["_p"][0])
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                return payload
        if "p" in params and params["p"]:
            decoded = decode_qr_p_token(params["p"][0])
            if decoded:
                return decoded

    return None


def _empty_rollcall(rollcall_id: int) -> "RollcallRecord":
    """构造一个仅有 rollcall_id 的空记录，用于二维码签到返回。"""
    return RollcallRecord(
        course_title="", created_by_name="", department_name="",
        is_expired=False, is_number=False, is_radar=False,
        rollcall_id=rollcall_id, rollcall_status="", scored=False, status="",
    )


# 服务端二维码签到常见错误码 -> 中文提示（错误码来自 c-mobile 前端逆向）
QR_ROLLCALL_ERROR_MESSAGES = {
    "ROLLCALL_DEVICE_ALREADY_IN_USE": "该设备已被其他学生使用签到，请更换设备或稍后再试。",
    "QR_ROLLCALL_UNKNOWN_STUDENT": "非本课程的学生无法签到，请先加入课程。",
    "QR_ROLLCALL_ALREADY_CLOSED": "二维码签到已结束。",
    "QR_ROLLCALL_NOT_FOUND": "找不到进行中的二维码签到。",
    "QR_ROLLCALL_NO_REQUEST_DATA": "缺少签到参数。",
    "QR_ROLLCALL_INVALID_QR_CREATE_TIME": "二维码时间验证失败，可能已刷新，请让老师重新展示二维码。",
    "QR_ROLLCALL_INVALID_CREATE_TIME_HASH": "签到时间与二维码生成时间不一致，请重新扫描。",
    "QR_ROLLCALL_CODE_EXPIRED": "二维码已过期，请刷新后重新扫描。",
}


QR_SIGNIN_LOG_FILE = CONFIG_DIR / "qr_signin.jsonl"


def _mask_username(username: str) -> str:
    username = str(username or "")
    if len(username) <= 2:
        return "*" * len(username)
    return username[0] + "*" * (len(username) - 2) + username[-1]


def _truncate_for_log(value: Any, max_len: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > max_len:
        text = text[:max_len] + "...(truncated)"
    return text


def log_qr_signin(record: Dict[str, Any]) -> None:
    """把二维码签到结果按 JSON Lines 追加到配置目录下，便于服务器端排查。"""
    try:
        ensure_config_dir()
        record.setdefault("time", datetime.now(CHINA_TZ).isoformat(timespec="seconds"))
        with open(QR_SIGNIN_LOG_FILE, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 日志写入失败不能影响签到主流程
        pass


def _opencv_decode_qr(image_bytes: bytes) -> Optional[str]:
    """用 OpenCV 解码二维码，比 pyzbar 更耐实拍/倾斜/模糊。"""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        return None

    candidates = [image]
    # 照片常带 EXIF 旋转信息，补测 90/180/270 三个方向
    rotated = image
    for _ in range(3):
        rotated = cv2.rotate(rotated, cv2.ROTATE_90_CLOCKWISE)
        candidates.append(rotated)

    detector = cv2.QRCodeDetector()
    for frame in candidates:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sources = [frame, gray]
        for source in sources:
            try:
                value, points, _ = detector.detectAndDecode(source)
            except cv2.error:
                continue
            if value:
                return value
            # 定位到但没读出：放大 2 倍重试（小图/模糊常见）
            if points is not None and len(points) == 4:
                scaled = cv2.resize(
                    source, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC
                )
                try:
                    value, _, _ = detector.detectAndDecode(scaled)
                except cv2.error:
                    continue
                if value:
                    return value
    return None


def _parse_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RollcallService:
    def __init__(self, account: Dict[str, Any], session_cache_key: Optional[str] = None):
        self.account = account
        self.session_cache_key = session_cache_key

    @property
    def display_name(self) -> str:
        return self.account.get("name") or self.account.get("username") or "未命名账号"

    @property
    def session_cache_path(self) -> str:
        cache_key = self.session_cache_key or self.account.get("id")
        return get_session_cache_path(cache_key)

    @staticmethod
    def validate_credentials(username: str, password: str) -> ValidationResult:
        session = xmulogin(type=3, username=username, password=password)
        if not session:
            raise RuntimeError("统一认证登录失败，请检查学号和密码。")

        session.headers.update(DEFAULT_HEADERS)
        try:
            profile = RollcallService.fetch_profile(session)
        except Exception:
            profile = {}
        name = profile.get("name") or username
        return ValidationResult(session=session, profile=profile, name=name)

    def clear_session_cache(self) -> bool:
        cache_path = self.session_cache_path
        if os.path.exists(cache_path):
            os.remove(cache_path)
            return True
        return False

    def get_session(self) -> requests.Session:
        cache_path = self.session_cache_path

        if os.path.exists(cache_path):
            cached_session = requests.Session()
            cached_session.headers.update(DEFAULT_HEADERS)
            if load_session(cached_session, cache_path):
                profile = verify_session(cached_session)
                if profile:
                    return cached_session

        session = xmulogin(
            type=3,
            username=self.account["username"],
            password=self.account["password"],
        )
        if not session:
            raise RuntimeError("统一认证登录失败，请重新使用 /conf 更新账号信息。")

        session.headers.update(DEFAULT_HEADERS)
        save_session(session, cache_path)
        return session

    @staticmethod
    def fetch_profile(session: requests.Session) -> Dict[str, Any]:
        response = session.get(PROFILE_URL, headers=DEFAULT_HEADERS, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"获取用户信息失败，HTTP {response.status_code}")
        payload = _safe_json(response)
        if not isinstance(payload, dict):
            raise RuntimeError("获取用户信息失败，响应格式异常。")
        return payload

    def fetch_rollcalls(self, session: Optional[requests.Session] = None) -> List[RollcallRecord]:
        active_session = session or self.get_session()
        response = active_session.get(ROLLCALLS_URL, headers=DEFAULT_HEADERS, timeout=15)
        if response.status_code != 200:
            raise RuntimeError(f"查询签到失败，HTTP {response.status_code}")

        payload = _safe_json(response)
        rollcalls = payload.get("rollcalls", [])
        if not isinstance(rollcalls, list):
            raise RuntimeError("查询签到失败，返回数据缺少 rollcalls 列表。")

        return [RollcallRecord.from_api(item) for item in rollcalls]

    def answer_active_rollcalls(self) -> AnswerBatchResult:
        session = self.get_session()
        rollcalls = self.fetch_rollcalls(session=session)
        outcomes = [self.answer_rollcall(session, rollcall) for rollcall in rollcalls]
        return AnswerBatchResult(
            account=self.account,
            queried_at=datetime.now(CHINA_TZ),
            rollcalls=rollcalls,
            outcomes=outcomes,
        )

    def answer_rollcall(self, session: requests.Session, rollcall: RollcallRecord) -> AnswerOutcome:
        if rollcall.is_expired:
            return AnswerOutcome(
                rollcall=rollcall,
                action="expired",
                success=False,
                message="该签到已过期。",
            )

        if rollcall.status == "on_call_fine":
            return AnswerOutcome(
                rollcall=rollcall,
                action="already_answered",
                success=True,
                message="该签到已完成。",
            )

        if rollcall.is_radar:
            return self._answer_radar_rollcall(session, rollcall)

        if rollcall.is_number and rollcall.status == "absent":
            return self._answer_number_rollcall(session, rollcall)

        if rollcall.is_number:
            return AnswerOutcome(
                rollcall=rollcall,
                action="skipped",
                success=False,
                message=f"当前状态为 {rollcall.status or 'unknown'}，未执行数字签到。",
            )

        return AnswerOutcome(
            rollcall=rollcall,
            action="unsupported",
            success=False,
            message="二维码签到暂不支持自动应答。",
        )

    def _answer_number_rollcall(
        self,
        session: requests.Session,
        rollcall: RollcallRecord,
    ) -> AnswerOutcome:
        code_url = f"{BASE_URL}/api/rollcall/{rollcall.rollcall_id}/student_rollcalls"
        answer_url = f"{BASE_URL}/api/rollcall/{rollcall.rollcall_id}/answer_number_rollcall"

        try:
            code_response = session.get(code_url, headers=session.headers, timeout=15)
        except requests.RequestException as exc:
            return AnswerOutcome(
                rollcall=rollcall,
                action="failed",
                success=False,
                message=f"获取签到码失败：{exc}",
            )

        if code_response.status_code != 200:
            return AnswerOutcome(
                rollcall=rollcall,
                action="failed",
                success=False,
                message=f"获取签到码失败，HTTP {code_response.status_code}",
                response_status=code_response.status_code,
                raw_data=_safe_json(code_response),
            )

        number_code = find_number_code(_safe_json(code_response))
        if not number_code:
            return AnswerOutcome(
                rollcall=rollcall,
                action="failed",
                success=False,
                message="获取签到码失败，响应中没有 number_code。",
            )

        payload = {
            "deviceId": str(uuid.uuid4()),
            "numberCode": number_code,
        }

        try:
            response = session.put(answer_url, json=payload, headers=session.headers, timeout=15)
        except requests.RequestException as exc:
            return AnswerOutcome(
                rollcall=rollcall,
                action="failed",
                success=False,
                message=f"提交数字签到失败：{exc}",
                number_code=number_code,
            )

        if response.status_code == 200:
            return AnswerOutcome(
                rollcall=rollcall,
                action="answered",
                success=True,
                message="数字签到成功。",
                number_code=number_code,
                response_status=200,
            )

        return AnswerOutcome(
            rollcall=rollcall,
            action="failed",
            success=False,
            message=f"提交数字签到失败，HTTP {response.status_code}",
            number_code=number_code,
            response_status=response.status_code,
            raw_data=_safe_json(response),
        )

    def _answer_radar_rollcall(
        self,
        session: requests.Session,
        rollcall: RollcallRecord,
    ) -> AnswerOutcome:
        url = f"{BASE_URL}/api/rollcall/{rollcall.rollcall_id}/answer"
        probe_points = [
            (24.3, 118.0),
            (24.6, 118.2),
        ]

        probe_results = []
        for latitude, longitude in probe_points:
            response = session.put(
                url,
                json=self._build_radar_payload(latitude, longitude),
                headers=DEFAULT_HEADERS,
                timeout=15,
            )
            payload = _safe_json(response)
            probe_results.append((latitude, longitude, response.status_code, payload))
            if response.status_code == 200:
                return AnswerOutcome(
                    rollcall=rollcall,
                    action="answered",
                    success=True,
                    message="雷达签到成功。",
                    latitude=latitude,
                    longitude=longitude,
                    response_status=200,
                )

        first_distance = _parse_float(probe_results[0][3].get("distance"))
        second_distance = _parse_float(probe_results[1][3].get("distance"))
        if first_distance is None or second_distance is None:
            return AnswerOutcome(
                rollcall=rollcall,
                action="failed",
                success=False,
                message="雷达签到失败，服务端未返回距离信息。",
                response_status=probe_results[-1][2],
                raw_data=probe_results[-1][3],
            )

        solved_points = self._solve_two_points(
            probe_results[0][0],
            probe_results[0][1],
            probe_results[1][0],
            probe_results[1][1],
            first_distance,
            second_distance,
        )
        if not solved_points:
            return AnswerOutcome(
                rollcall=rollcall,
                action="failed",
                success=False,
                message="雷达签到失败，无法求解坐标。",
                response_status=probe_results[-1][2],
                raw_data=probe_results[-1][3],
            )

        last_response_status = probe_results[-1][2]
        last_payload = probe_results[-1][3]
        for latitude, longitude in solved_points:
            response = session.put(
                url,
                json=self._build_radar_payload(latitude, longitude),
                headers=DEFAULT_HEADERS,
                timeout=15,
            )
            payload = _safe_json(response)
            last_response_status = response.status_code
            last_payload = payload
            if response.status_code == 200:
                return AnswerOutcome(
                    rollcall=rollcall,
                    action="answered",
                    success=True,
                    message="雷达签到成功。",
                    latitude=latitude,
                    longitude=longitude,
                    response_status=200,
                )

        return AnswerOutcome(
            rollcall=rollcall,
            action="failed",
            success=False,
            message=f"雷达签到失败，HTTP {last_response_status}",
            response_status=last_response_status,
            raw_data=last_payload,
        )

    @staticmethod
    def _build_radar_payload(latitude: float, longitude: float) -> Dict[str, Any]:
        return {
            "accuracy": 35,
            "altitude": 0,
            "altitudeAccuracy": None,
            "deviceId": str(uuid.uuid4()),
            "heading": None,
            "latitude": latitude,
            "longitude": longitude,
            "speed": None,
        }

    @staticmethod
    def _latlon_to_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple:
        radius = 6371000
        x = math.radians(lon - lon0) * radius * math.cos(math.radians(lat0))
        y = math.radians(lat - lat0) * radius
        return x, y

    @staticmethod
    def _xy_to_latlon(x: float, y: float, lat0: float, lon0: float) -> tuple:
        radius = 6371000
        latitude = lat0 + math.degrees(y / radius)
        longitude = lon0 + math.degrees(x / (radius * math.cos(math.radians(lat0))))
        return latitude, longitude

    @staticmethod
    def _circle_intersections(
        x1: float,
        y1: float,
        d1: float,
        x2: float,
        y2: float,
        d2: float,
    ) -> Optional[List[tuple]]:
        distance = math.hypot(x2 - x1, y2 - y1)
        if distance == 0:
            return None
        if distance > d1 + d2 or distance < abs(d1 - d2):
            return None

        a_value = (d1 ** 2 - d2 ** 2 + distance ** 2) / (2 * distance)
        h_square = d1 ** 2 - a_value ** 2
        if h_square < 0:
            return None
        h_value = math.sqrt(h_square)

        xm = x1 + a_value * (x2 - x1) / distance
        ym = y1 + a_value * (y2 - y1) / distance

        rx = -(y2 - y1) * (h_value / distance)
        ry = (x2 - x1) * (h_value / distance)

        return [
            (xm + rx, ym + ry),
            (xm - rx, ym - ry),
        ]

    @classmethod
    def _solve_two_points(
        cls,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        dist1: float,
        dist2: float,
    ) -> Optional[List[tuple]]:
        lat0 = (lat1 + lat2) / 2
        lon0 = (lon1 + lon2) / 2
        x1, y1 = cls._latlon_to_xy(lat1, lon1, lat0, lon0)
        x2, y2 = cls._latlon_to_xy(lat2, lon2, lat0, lon0)

        intersections = cls._circle_intersections(x1, y1, dist1, x2, y2, dist2)
        if not intersections:
            return None

        return [cls._xy_to_latlon(x, y, lat0, lon0) for x, y in intersections]

    # ---- QR 码签到支持 ----

    @staticmethod
    def decode_qr_image(image_bytes: bytes) -> Optional[str]:
        """解码二维码图片，返回内容文本。

        优先使用 pyzbar + pillow；读不出时自动回退到 OpenCV
        （更耐实拍、倾斜、模糊的签到码照片）。
        """
        pyzbar_ready = False
        try:
            from PIL import Image
            from pyzbar.pyzbar import decode as qr_decode
        except ImportError:
            pyzbar_ready = False
        else:
            pyzbar_ready = True

        if pyzbar_ready:
            try:
                img = Image.open(io.BytesIO(image_bytes))
                for source in (img, img.convert("L")):
                    try:
                        decoded = qr_decode(source)
                    except Exception:
                        decoded = []
                    if decoded:
                        return decoded[0].data.decode("utf-8", errors="replace")
            except Exception:
                pass

        content = _opencv_decode_qr(image_bytes)
        if content:
            return content

        if not pyzbar_ready:
            raise RuntimeError(
                "解码二维码需要 pyzbar+pillow 或 opencv-python-headless，当前均未安装。"
            )
        return None

    @staticmethod
    def parse_qr_content(content: str) -> Optional[Dict[str, Any]]:
        """解析扫码文本为签到字段字典，支持 JSON 与 /j?p= URL 两种格式。"""
        return parse_qr_content(content)

    def answer_qr_rollcall_with_code(
        self, session: requests.Session, rollcall_id: int, qr_content: str
    ) -> AnswerOutcome:
        """用解码出的二维码内容提交 QR 签到。

        二维码内容可能是 JSON，也可能是 c-mobile 的 /j?p= URL。方法会从内容里
        解析出 rollcallId 与 data 后，调用服务端 PUT /api/rollcall/{id}/
        answer_qr_rollcall 提交 {data, deviceId}。当调用方已显式传 rollcall_id
        且内容解析不出 rollcallId 时，则按调用方传入的 id 提交。
        """
        parsed = parse_qr_content(qr_content)
        data_value: Optional[str] = None
        if isinstance(parsed, dict):
            if parsed.get("rollcallId") is not None:
                rollcall_id = int(parsed["rollcallId"])
            if parsed.get("data") is not None:
                data_value = str(parsed["data"])
        if not data_value:
            # 兼容旧用法：内容本身就是一个签到 token/口令
            data_value = qr_content.strip()
        if not rollcall_id:
            return AnswerOutcome(
                rollcall=_empty_rollcall(0),
                action="failed",
                success=False,
                message="无法从二维码内容解析出签到编号 rollcallId。",
            )

        return self._submit_qr_answer(session, rollcall_id, data_value)

    def answer_qr_content(
        self, session: requests.Session, qr_content: str
    ) -> AnswerOutcome:
        """直接按二维码内容签到（rollcallId 由内容解析得出）。"""
        return self.answer_qr_rollcall_with_code(session, 0, qr_content)

    def _submit_qr_answer(
        self, session: requests.Session, rollcall_id: int, data_value: str
    ) -> AnswerOutcome:
        answer_url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer_qr_rollcall"
        payload = {
            "data": data_value,
            "deviceId": str(uuid.uuid4()),
        }

        try:
            response = session.put(answer_url, json=payload, headers=DEFAULT_HEADERS, timeout=15)
        except requests.RequestException as exc:
            outcome = AnswerOutcome(
                rollcall=_empty_rollcall(rollcall_id),
                action="failed",
                success=False,
                message=f"提交二维码签到失败：{exc}",
            )
            raw_data: Optional[Dict[str, Any]] = {"exception": str(exc)}
            response_status: Optional[int] = None
        else:
            data = _safe_json(response)
            response_status = response.status_code
            error_code = (
                data.get("errorCode")
                or data.get("error_code")
                or data.get("code")
                or ""
            )
            if isinstance(error_code, dict):
                error_code = ""
            error_key = str(error_code).upper()
            mapped_message = QR_ROLLCALL_ERROR_MESSAGES.get(error_key)

            if response.status_code == 200:
                # 会话过期时后端可能把请求 302 到统一认证登录页，最终返回 HTML 200，
                # 此时不能当作签到成功
                if "text" in data and len(data) == 1:
                    outcome = AnswerOutcome(
                        rollcall=_empty_rollcall(rollcall_id),
                        action="failed",
                        success=False,
                        message="登录已过期，请先重新发送 /qr（或 /refresh）后再签到。",
                        number_code=data_value,
                        response_status=200,
                        raw_data=data,
                    )
                elif mapped_message:
                    outcome = AnswerOutcome(
                        rollcall=_empty_rollcall(rollcall_id),
                        action="failed",
                        success=False,
                        message=f"二维码签到失败：{mapped_message}",
                        number_code=data_value,
                        response_status=200,
                        raw_data=data,
                    )
                else:
                    outcome = AnswerOutcome(
                        rollcall=_empty_rollcall(rollcall_id),
                        action="answered",
                        success=True,
                        message="二维码签到成功！",
                        number_code=data_value,
                        response_status=200,
                    )
            else:
                if not mapped_message:
                    mapped_message = (
                        data.get("message")
                        or data.get("msg")
                        or f"HTTP {response.status_code}"
                    )
                outcome = AnswerOutcome(
                    rollcall=_empty_rollcall(rollcall_id),
                    action="failed",
                    success=False,
                    message=f"二维码签到失败：{mapped_message}",
                    number_code=data_value,
                    response_status=response.status_code,
                    raw_data=data,
                )
            raw_data = data

        # 只在失败时留日志，成功不写，减少无用记录与隐私数据落盘
        if not outcome.success:
            log_qr_signin(
                {
                    "event": "qr_signin",
                    "account_id": int(self.account.get("id") or 0),
                    "account_name": self.display_name,
                    "username_masked": _mask_username(self.account.get("username") or ""),
                    "rollcall_id": rollcall_id,
                    "success": False,
                    "message": outcome.message,
                    "response_status": response_status,
                    "response": _truncate_for_log(raw_data),
                }
            )
        return outcome
