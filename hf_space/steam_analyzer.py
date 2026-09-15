import datetime as dt
import html
import json
import os
import re
import time
import urllib.parse

from dotenv import load_dotenv

load_dotenv()


class BigPickleError(Exception):
    pass


class InvalidInputError(BigPickleError):
    pass


class ProfileNotFoundError(BigPickleError):
    pass


class SteamUnavailableError(BigPickleError):
    pass


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 BigPickle-osint-bot/1.0"
TIMEOUT = 20
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")

try:
    from curl_cffi import requests as _http
except ImportError:
    import requests as _http
    _HTTP_IMPRESSONATE = False
else:
    _HTTP_IMPRESSONATE = True

_STEAMID64_RE = re.compile(r"^(\d{15,18})$")
_PROFILE_RE = re.compile(r"steamcommunity\.com/profiles/(\d{15,18})", re.I)
_VANITY_URL_RE = re.compile(r"steamcommunity\.com/id/([A-Za-z0-9_\-]+)", re.I)
_BARE_VANITY_RE = re.compile(r"^([A-Za-z0-9_\-]{3,32})$")

FOCUS_APPIDS = {252490: "Rust", 730: "CS2", 570: "Dota 2"}
FOCUS_ALIASES = {
    "rust": "Rust",
    "counter-strike 2": "CS2",
    "counter‑strike 2": "CS2",
    "dota 2": "Dota 2",
}


def _get(url, params=None, _attempts=3):
    kwargs = {"timeout": TIMEOUT}
    if _HTTP_IMPRESSONATE:
        kwargs["impersonate"] = "chrome"
    last_exc = None
    for attempt in range(_attempts):
        try:
            resp = _http.get(url, params=params, headers={"User-Agent": USER_AGENT}, **kwargs)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            last_exc = exc
            if attempt < _attempts - 1:
                time.sleep(0.6 * (attempt + 1))
    raise SteamUnavailableError() from last_exc


def _tag(text, name):
    m = re.search(
        r"<%s>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</%s>" % (name, name),
        text, re.I | re.S,
    )
    if m and m.group(1).strip():
        return html.unescape(m.group(1).strip())
    return None


def _get_json(url, params=None):
    return json.loads(_get(url, params=params))


def resolve_steamid(raw: str) -> str:
    s = raw.strip().strip("<>").strip()
    m = _STEAMID64_RE.match(s)
    if m:
        return m.group(1)
    m = _PROFILE_RE.search(s)
    if m:
        return m.group(1)
    v = _VANITY_URL_RE.search(s)
    if v is None:
        v = _BARE_VANITY_RE.match(s)
    if v:
        vanity = v.group(1)
        xml = _get("https://steamcommunity.com/id/%s/?xml=1" % vanity)
        sid = _tag(xml, "steamID64")
        if sid:
            return sid
        raise ProfileNotFoundError()
    raise InvalidInputError(
        "❌ Неверный ввод.\n"
        "Присылай SteamID64 (17 цифр) или ссылку на профиль:\n"
        "<code>https://steamcommunity.com/id/nickname</code>\n"
        "<code>https://steamcommunity.com/profiles/76561198...</code>"
    )


def _label(game_name, appid):
    label = FOCUS_APPIDS.get(appid) or FOCUS_ALIASES.get((game_name or "").lower())
    return label or game_name


def _get_game_hours(steamid64):
    if not STEAM_API_KEY:
        return {}
    try:
        data = _get_json(
            "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/",
            params={
                "key": STEAM_API_KEY,
                "steamid": steamid64,
                "include_played_free_games": True,
                "include_appinfo": True,
                "format": "json",
            },
        )
    except Exception:
        return {}
    hours = {}
    for g in (data.get("response", {}).get("games") or []):
        hrs = (g.get("playtime_forever") or 0) / 3600.0
        if hrs < 0.1:
            continue
        label = _label(g.get("name"), g.get("appid"))
        hours[label] = hrs
    return hours


_RECENT_GAME_RE = re.compile(
    r'<div class="game_info_cap[^"]*"><a href="https://steamcommunity\.com/app/(\d+)">'
    r'.*?</a></div>\s*<div class="game_info_details">\s*(.*?)</div>\s*'
    r'<div class="game_name"><a[^>]*>(.*?)</a>',
    re.S | re.I,
)


def _get_recent_hours(steamid64):
    try:
        html_page = _get("https://steamcommunity.com/profiles/%s" % steamid64)
    except SteamUnavailableError:
        return {}
    hours = {}
    for m in _RECENT_GAME_RE.finditer(html_page):
        appid = int(m.group(1))
        details = re.sub(r"<[^>]+>", " ", m.group(2))
        hfound = re.search(r"([\d.,]+)\s*hrs?\s*on\s*record", details, re.I)
        name = html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        if not hfound or not name:
            continue
        try:
            hrs = float(hfound.group(1).replace(",", "."))
        except ValueError:
            continue
        label = _label(name, appid)
        hours[label] = max(hours.get(label, 0.0), hrs)
    return hours


def _get_player_bans(steamid64):
    if not STEAM_API_KEY:
        return None
    try:
        data = _get_json(
            "https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/",
            params={"key": STEAM_API_KEY, "steamids": steamid64, "format": "json"},
        )
        players = data.get("players") or []
        if not players:
            return None
        p = players[0]
        return {
            "vac_banned": p.get("VACBanned", False),
            "num_vac_bans": p.get("NumberOfVACBans", 0),
            "num_game_bans": p.get("NumberOfGameBans", 0),
            "community_banned": p.get("CommunityBanned", False),
            "economy_ban": (p.get("EconomyBan") or "none").lower(),
            "days_since_last_ban": p.get("DaysSinceLastBan"),
        }
    except Exception:
        return None


def fetch_profile(steamid64: str) -> dict:
    xml = _get("https://steamcommunity.com/profiles/%s/?xml=1" % steamid64)
    sid = _tag(xml, "steamID64")
    if not sid:
        raise ProfileNotFoundError()

    privacy = (_tag(xml, "privacyState") or "public").lower()
    if _tag(xml, "isPrivateAccount") == "1":
        privacy = "private"

    ban = _get_player_bans(sid)
    vac_banned = bool(_tag(xml, "vacBanned") and _tag(xml, "vacBanned") != "0")
    if ban is not None:
        vac_banned = ban["vac_banned"]
        num_vac = ban["num_vac_bans"]
        num_game_bans = ban["num_game_bans"]
        community_banned = ban["community_banned"]
        trade_ban = ban["economy_ban"]
    else:
        num_vac = 1 if vac_banned else 0
        num_game_bans = 0
        community_banned = (_tag(xml, "communityBanned") or "0") != "0"
        trade_ban = (_tag(xml, "tradeBanState") or "none").lower()

    hours = _get_game_hours(sid) if privacy == "public" else {}
    hours_source = "api" if hours else None
    if privacy == "public":
        for k, v in _get_recent_hours(sid).items():
            if k not in hours:
                hours[k] = v
                hours_source = hours_source or "recent"

    return {
        "steamid64": sid,
        "name": _tag(xml, "steamID") or _tag(xml, "customURL") or sid,
        "custom_url": _tag(xml, "customURL"),
        "realname": _tag(xml, "realname"),
        "member_since": _tag(xml, "memberSince"),
        "privacy": privacy,
        "online_state": _tag(xml, "onlineState") or "offline",
        "state_message": _tag(xml, "stateMessage") or "—",
        "hours_2wk": _tag(xml, "hoursPlayed2Wk"),
        "limited_account": (_tag(xml, "isLimitedAccount") or "0") != "0",
        "vac_banned": vac_banned,
        "num_vac_bans": num_vac,
        "num_game_bans": num_game_bans,
        "trade_ban": trade_ban,
        "community_banned": community_banned,
        "has_ban_api": ban is not None,
        "hours": hours,
        "hours_source": hours_source,
    }


def _account_age_days(member_since):
    if not member_since:
        return None
    for fmt in ("%B %d, %Y", "%d %B, %Y", "%d %B %Y", "%d/%m/%Y"):
        try:
            d = dt.datetime.strptime(member_since.strip(), fmt).date()
            return (dt.date.today() - d).days
        except ValueError:
            continue
    return None


def _verdict(score):
    if score <= 1.5:
        return "🟢 НИЗКИЙ"
    if score <= 3.0:
        return "🟡 СРЕДНИЙ"
    if score <= 5.0:
        return "🟠 ВЫСОКИЙ"
    return "🔴 КРИТИЧЕСКИЙ"


def analyze(profile):
    score = 0.0
    flags = []

    if profile["num_vac_bans"]:
        score += 3
        flags.append("VAC-баны в истории (всего %d)" % profile["num_vac_bans"])
    if profile["num_game_bans"]:
        score += 2
        flags.append("Game Bans: %d блокировок" % profile["num_game_bans"])
    if profile["community_banned"]:
        score += 2
        flags.append("Community ban — ограничен в комьюнити Steam")
    if profile["trade_ban"] and profile["trade_ban"] != "none":
        score += 1
        flags.append("Активная торговая блокировка Economy Ban")

    if profile["privacy"] != "public":
        score += 2
        flags.append("Скрытая телеметрия — приватный профиль мешает проверке")

    if profile["limited_account"]:
        score += 0.5
        flags.append("Ограниченный аккаунт (Limited) — маркер смурфа/свежего профиля")

    if profile["hours_2wk"]:
        try:
            h2 = float(profile["hours_2wk"])
            if h2 >= 60:
                score += 0.5
                flags.append("Интенсивная активность: %.0f ч за 2 недели" % h2)
        except ValueError:
            pass

    age_days = _account_age_days(profile["member_since"])
    total_focus = sum(h for k, h in profile["hours"].items() if k in ("Rust", "CS2", "Dota 2"))
    keys = list(profile["hours"].keys())

    if age_days is not None:
        if age_days < 90:
            flags.append("Аккаунт моложе 3 месяцев")
        if age_days < 180 and total_focus >= 500:
            score += 2
            flags.append("Аномально высокий темп наигранности для молодого аккаунта (возможен буст/шеринг)")
        if age_days < 365 and total_focus >= 1500:
            score += 1.5
            flags.append("Огромная наигранность при аккаунте младше года — риск смурфа / нелегитимных активов")
        if age_days < 180 and profile["limited_account"]:
            score += 0.5

    if not flags:
        if age_days is not None and age_days > 5 * 365:
            summary = "Профиль чистый: возраст солидный, банов нет, наигранность непротиворечива. Низкий фактор риска для соревновательной среды."
        else:
            summary = "Видимых маркеров риска не обнаружено. Профиль не вызывает подозрений."
    else:
        summary = "Выявлены маркеры, требующие внимания: %s." % "; ".join(flags[:4])

    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "flags": flags,
        "summary": summary,
    }


def _fmt_hours(h):
    if h < 10:
        return "%.1f ч" % round(h, 1)
    return "%s ч" % "{:,}".format(int(round(h))).replace(",", " ")


def build_card(profile):
    private = profile["privacy"] != "public"
    risk = analyze(profile)
    name = html.escape(profile["name"] or profile["steamid64"])
    sid = profile["steamid64"]
    profile_url = "https://steamcommunity.com/profiles/%s" % sid
    status_txt = "Приватный" if private else "Публичный"
    age_txt = html.escape(profile["member_since"] or "нет данных")

    L = []
    if private:
        L.append("🔒 <b>[ЗАКРЫТЫЙ / ПРИВАТНЫЙ ПРОФИЛЬ]</b>")
    L.append("🎯 Целевая разведка: <b>%s</b>" % name)
    L.append("    SteamID64: <code>%s</code>" % sid)
    L.append("    Ссылка на профиль: <a href=\"%s\">Открыть в Steam</a>" % profile_url)
    L.append("    Возраст аккаунта / Статус: %s | %s" % (age_txt, status_txt))
    if profile["realname"]:
        L.append("    Регистрационные данные: %s" % html.escape(profile["realname"]))
    L.append("")

    L.append("⏱️ Игровая телеметрия (Часы и активность)")
    if private:
        L.append("    Основная игра (Rust/CS2/Dota 2): скрыто приватностью")
    else:
        focus = {k: h for k, h in profile["hours"].items() if k in ("Rust", "CS2", "Dota 2")}
        if focus:
            for label, h in focus.items():
                L.append("    %s: <b>%s</b>" % (label, _fmt_hours(h)))
            others = [(k, h) for k, h in profile["hours"].items() if k not in ("Rust", "CS2", "Dota 2")]
            if others:
                top = [k for k, _ in sorted(others, key=lambda x: -x[1])[:3]]
                L.append("    Другое: %s" % ", ".join(html.escape(t) for t in top))
        elif profile["hours"]:
            top = sorted(profile["hours"].items(), key=lambda x: -x[1])[:3]
            L.append("    Топ игр: %s" % "; ".join("%s (%s)" % (html.escape(n), _fmt_hours(h)) for n, h in top))
        else:
            L.append("    Основная игра (Rust/CS2/Dota 2): нет публичных часов")
    if profile["hours_source"] == "recent":
        L.append("    (часы — за последнюю активность, полный список — со Steam API key)")
    state = html.escape(profile["state_message"] or "—")
    if profile["hours_2wk"]:
        try:
            h2 = float(profile["hours_2wk"])
            if h2 > 0:
                state = "%s · наиграно %.0f ч за 2 недели" % (state, h2)
        except ValueError:
            pass
    L.append("    Последняя активность: %s" % state)
    L.append("")

    L.append("🛡️ Проверка безопасности и доверия")
    if profile["num_vac_bans"]:
        L.append("    VAC-баны: <b>%d в истории</b>%s"
                 % (profile["num_vac_bans"], "" if profile["has_ban_api"] else " (бинарно, точное число — через Steam API key)"))
    else:
        L.append("    VAC-баны: Чисто ✔️")
    bans_parts = []
    if profile["num_game_bans"]:
        bans_parts.append("Game Bans: %d" % profile["num_game_bans"])
    if profile["trade_ban"] and profile["trade_ban"] != "none":
        bans_parts.append("Торговая: %s" % profile["trade_ban"])
    if profile["community_banned"]:
        bans_parts.append("Community ban")
    if not bans_parts and not profile["has_ban_api"] and not profile["num_vac_bans"]:
        L.append("    Игровая / Торговая блокировка: Чисто ✔️")
    elif not bans_parts:
        L.append("    Игровая / Торговая блокировка: Нет ✔️")
    else:
        L.append("    Игровая / Торговая блокировка: %s" % html.escape(", ".join(bans_parts)))
    if profile["limited_account"]:
        L.append("    Ограниченный аккаунт (Limited): Да")
    L.append("")

    L.append("🤖 Анализ рисков (Big Pickle AI-оценка)")
    L.append("    Уровень риска: <b>%s</b> (оценка %.1f)" % (risk["verdict"], risk["score"]))
    for f in risk["flags"]:
        L.append("    • %s" % html.escape(f))
    L.append("    %s" % html.escape(risk["summary"]))
    L.append("")

    L.append("🔍 Внешние архивы и трекеры")
    L.append("    • SteamID.uk: <a href=\"https://steamid.uk/profile/%s\">Открыть профиль</a> (VAC / EAC / BattlEye / торговля)" % sid)
    L.append("    • RustBans: <a href=\"https://rustbans.com/results.php?steam_id=%s\">Проверить в базе Rust</a>" % sid)
    q = urllib.parse.quote(profile["name"] or sid)
    L.append("    • Faceit: <a href=\"https://www.google.com/search?q=%%22%s%%22+site%%3Afaceit.com%%2Fen%%2Fplayers\">Поиск по Faceit</a>" % q)
    L.append("    • SteamRep закрыт (sunset) — репутацию смотри в SteamID.uk / Google по нику")
    L.append("    • BattlEye / EAC: агрегировано в SteamID.uk (вкладка Bans)")

    return "\n".join(L)