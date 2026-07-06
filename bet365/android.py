import base64
import hashlib
import os
import threading
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Optional, Union

from curl_cffi.requests import Session, get
from tls_client import Session as BogdanSession

from .message_parser import fix_data, get_parsers, pretty_print_table, read_table
from .sdk import build_cookies

Bet365ZAPConnection = None

try:
    from .live import Bet365ZAPConnection

    IS_ZAP_AVAILABLE = True
except ImportError:  # not available for demo yet
    IS_ZAP_AVAILABLE = False


def NOT_NULL(_, m):
    return m is not None


@dataclass
class Sport:
    name: str
    PD: str


TLS_FINGERPRINT = {
    "ja3": "771,4865-4866-4867-49195-49196-52393-49199-49200-52392-49171-49172-156-157-47-53,0-23-65281-10-11-35-16-5-13-51-45-43-21,29-23-24,0",
    "akamai": "4:16777216|16711681|0|m,p,a,s",
    "extra_fp": {
        "tls_signature_algorithms": [
            "ecdsa_secp256r1_sha256",
            "rsa_pss_rsae_sha256",
            "rsa_pkcs1_sha256",
            "ecdsa_secp384r1_sha384",
            "rsa_pss_rsae_sha384",
            "rsa_pkcs1_sha384",
            "rsa_pss_rsae_sha512",
            "rsa_pkcs1_sha512",
            "rsa_pkcs1_sha1",
        ]
    },
}


class Bet365AndroidSession:
    def __init__(
        self,
        api_url: str,
        api_key: str,
        *args,
        host="www.bet365.com",
        proxy: Optional[str] = None,
        verify=True,
        **kwargs,
    ):
        kwargs.update(TLS_FINGERPRINT)
        self.session = Session(*args, **kwargs)
        self.session.proxies = {}
        if proxy:
            self.session.proxies["https"] = proxy
        self.session.verify = False
        self.proxy = proxy
        self.verify = False
        self.host = host
        self.api_url = api_url
        self.api_key = api_key
        self._cookie_lock = threading.Lock()
        self.device_id = f"00000000-0000-0000-{os.urandom(2).hex().upper()}-{os.urandom(6).hex().upper()}"
        self._sst = ""
        self.zap_thread = None
    def get_sport_homepage(self, sport: Sport):
        splash_response = self.protected_get(
            f"https://{self.host}/splashcontentapi/getsplashpods",
            params={
                "lid": "1",
                "zid": "9",
                "pd": sport.PD,
                "cid": "143",
                "cgid": "1",
                "ctid": "143",
                "tzo": "60",
            },
            headers={
                "User-Agent": "Mozilla (Linux; Android 12 Phone; CPU M2003J15SC OS 12 like Gecko) Chrome/144.0.7559.59 Gen6 bet365/8.0.36.00",
                "X-b365App-ID": "8.0.36.00-row",
                "Host": self.host,
                "Connection": "Keep-Alive",
                "Accept-Encoding": "gzip",
            },
        )
        match_tables = []
        for parser in get_parsers(splash_response.text):
            for _, _ in parser.find_sections(
                "CL",
                PV=lambda k, v: v.startswith("podcontentcontentapi"),
                include_part_index=True,
            ):
                for idx, _ in parser.find_sections("MG", include_part_index=True):
                    table = read_table(parser, idx)
                    pretty_print_table(table)
                    match_tables.append(fix_data(table))

    def extract_available_sports(self) -> list[Sport]:
        r = self.protected_get(
            f"https://{self.host}/leftnavcontentapi/allsportsmenu",
            params={
                "lid": "30",
                "zid": "0",
                "pd": "#AL#B1#R^1#",
                "cid": "13",
                "cgid": "2",
                "ctid": "13",
                "tzo": "660",
            },
            headers={
                "User-Agent": "Mozilla (Linux; Android 12 Phone; CPU M2003J15SC OS 12 like Gecko) Chrome/144.0.7559.59 Gen6 bet365/8.0.36.00",
                "X-b365App-ID": "8.0.36.00-row",
                "Accept-Encoding": "gzip",
            },
            verify=False,
        )
        sports = []
        for parser in get_parsers(r.text):
            for _, cl in parser.find_sections(
                "CL", PD=NOT_NULL, NA=NOT_NULL, include_part_index=True
            ):
                pd = cl.get_property("PD", "")
                if pd.endswith("K^5#"):
                    pd = pd[: -len("K^5#")]
                sports.append(Sport(cl.get_property("NA"), pd))
        return sports

    def go_homepage(self):
        homepage_response = self.session.get(
            f"https://{self.host}/",
            headers={
                "x-b365app-id": "8.0.36.00-row",
                "sec-ch-ua": '"Android WebView";v="144", "Not?A_Brand";v="8", "Chromium";v="144"" Gen6 "',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "upgrade-insecure-requests": "1",
                "user-agent": "Mozilla (Linux; Android 12 Phone; CPU M2003J15SC OS 12 like Gecko) Chrome/144.0.7559.59 Gen6 bet365/8.0.36.00",
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "x-requested-with": "com.bet365Wrapper.Bet365_Application",
                "sec-fetch-site": "none",
                "sec-fetch-mode": "navigate",
                "sec-fetch-user": "?1",
                "sec-fetch-dest": "document",
                "accept-encoding": "gzip, deflate, br, zstd",
                "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "referer": f"https://{self.host}",
            },
            default_headers=False,
        )
        assert homepage_response.status_code == 200, (
            "Blocked by Cloudflare, bad IP or headers should be updated"
            if homepage_response.status_code == 403
            else f"Unknown error while going to homepage: {homepage_response.status_code}"
        )
        configuration_response = self.session.get(
            f"https://{self.host}"
            + homepage_response.text.split('"SITE_CONFIG_LOCATION":"')[1].split('"')[0],
            headers={
                "origin": f"https://{self.host}",
                "sec-ch-ua-platform": '"Windows"',
                "user-agent": "Mozilla (Linux; Android 12 Phone; CPU M2003J15SC OS 12 like Gecko) Chrome/144.0.7559.59 Gen6 bet365/8.0.36.00",
                "sec-ch-ua": '"Chromium";v="144", "Not=A?Brand";v="24", "Brave";v="140"',
                "sec-ch-ua-mobile": "?0",
                "accept": "*/*",
                "sec-gpc": "1",
                "accept-language": "tr-TR,tr;q=0.5",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
                "referer": f"https://{self.host}/",
                "accept-encoding": "gzip, deflate, br, zstd",
                "priority": "u=1, i",
            },
            default_headers=False,
        )
        assert configuration_response.status_code == 200, (
            "Blocked while getting configuration, probably bad IP"
            if configuration_response.status_code == 500
            else f"Unknown error while fetching configuration: {configuration_response.status_code}"
        )

        self._sst = configuration_response.json()["ns_weblib_util"]["WebsiteConfig"][
            "SST"
        ]
        self.session.cookies["usdi"] = f"uqid={self.device_id}"
        print(IS_ZAP_AVAILABLE)
        if IS_ZAP_AVAILABLE and Bet365ZAPConnection:
            self.zap_thread = Bet365ZAPConnection(
                "Mozilla (Linux; Android 12 Phone; CPU M2003J15SC OS 12 like Gecko) Chrome/144.0.7559.59 Gen6 bet365/8.0.36.00",
                self.session.cookies["pstk"],
                homepage_response.text,
                configuration_response.json(),
            ).start()

    def protected_get(
        self, url: str, headers: Union[dict[str, str], None] = None, *args, **kwargs
    ):
        headers = headers or {}

        cookie_header = build_cookies(self.session.cookies)

        headers["Cookie"] = cookie_header
        kwargs["default_headers"] = False
        kwargs.update(TLS_FINGERPRINT)

        params = kwargs.get("params", {})
        if len(params):
            parsed_url = urllib.parse.urlparse(url)
            path = (
                parsed_url.path + "?" + urllib.parse.urlencode(params)
                if len(params)
                else parsed_url.path
            )
            url = f"{parsed_url.scheme}://{parsed_url.netloc}{path}"
        headers["X-Net-Sync-Term-Android"] = self.get_x_net_header(
            url, cookie_header, b""
        )
        kwargs.update({"proxy": self.proxy, "verify": self.verify})
        response = get(url, headers=headers, *args, **kwargs)

        return response

    def get_x_net_header(self, url: str, cookie_header: str, post_data: bytes) -> str:
        response = BogdanSession().post(
            self.api_url,
            headers={"x-net-api-key": self.api_key},
            json={
                "url": url,
                "cookie": cookie_header,
                "post_hash": base64.b64encode(
                    hashlib.sha256(post_data).digest()
                ).decode(),
                "sst": self._sst,
                "device_id": self.device_id,
            },
        )
        assert response.status_code == 200, (
            "An error occured while generating token: " + response.text
        )
        return response.text
