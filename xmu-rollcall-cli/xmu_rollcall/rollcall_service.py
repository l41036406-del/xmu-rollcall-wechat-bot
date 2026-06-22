from __future__ import annotations

import io
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from xmulogin import xmulogin

from .config import get_session_cache_path
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
        """解码二维码图片，返回内容文本。需安装 pyzbar + pillow。"""
        try:
            from PIL import Image
            from pyzbar.pyzbar import decode as qr_decode
        except ImportError:
            raise RuntimeError("需要安装 pyzbar 和 pillow 库来解码二维码。")

        try:
            img = Image.open(io.BytesIO(image_bytes))
            decoded = qr_decode(img)
            if decoded:
                return decoded[0].data.decode("utf-8", errors="replace")
            return None
        except Exception as exc:
            raise RuntimeError(f"二维码解码失败：{exc}") from exc

    def answer_qr_rollcall_with_code(
        self, session: requests.Session, rollcall_id: int, qr_content: str
    ) -> AnswerOutcome:
        """用解码出的二维码内容提交 QR 签到。"""
        answer_url = f"{BASE_URL}/api/rollcall/{rollcall_id}/answer_qr_rollcall"

        payload = {
            "deviceId": str(uuid.uuid4()),
            "numberCode": qr_content.strip(),
        }

        try:
            response = session.put(answer_url, json=payload, headers=DEFAULT_HEADERS, timeout=15)
        except requests.RequestException as exc:
            return AnswerOutcome(
                rollcall=RollcallRecord(
                    course_title="", created_by_name="", department_name="",
                    is_expired=False, is_number=False, is_radar=False,
                    rollcall_id=rollcall_id, rollcall_status="", scored=False, status="",
                ),
                action="failed", success=False,
                message=f"提交二维码签到失败：{exc}",
            )

        if response.status_code == 200:
            return AnswerOutcome(
                rollcall=RollcallRecord(
                    course_title="", created_by_name="", department_name="",
                    is_expired=False, is_number=False, is_radar=False,
                    rollcall_id=rollcall_id, rollcall_status="", scored=False, status="",
                ),
                action="answered", success=True,
                message="二维码签到成功！",
                number_code=qr_content.strip(),
                response_status=200,
            )

        data = _safe_json(response)
        err_msg = data.get("message", data.get("error_code", f"HTTP {response.status_code}"))
        return AnswerOutcome(
            rollcall=RollcallRecord(
                course_title="", created_by_name="", department_name="",
                is_expired=False, is_number=False, is_radar=False,
                rollcall_id=rollcall_id, rollcall_status="", scored=False, status="",
            ),
            action="failed", success=False,
            message=f"二维码签到失败：{err_msg}",
            number_code=qr_content.strip(),
            response_status=response.status_code,
            raw_data=data,
        )
