"""いらすとや MCP Server（irasutoya-mcp）

替助人工作者在「いらすとや」找插圖，用來做衛教單、學習單、簡報。

為什麼需要這支：
  いらすとや 是日本的免費插圖庫，台灣的醫院診所衛教單大量在用。
  但它**只吃日文關鍵字**——你想找「注意力不集中」的插圖，
  站上要打的是「集中できない」或「気が散る」，
  而心理師不會知道要打什麼。
  這支 MCP 的價值不在幫你連網，在**幫你跨過那道語言牆**。

技術上：
  いらすとや 是 Blogger 架的，有標準的 feed API，不用爬 HTML。

      GET /feeds/posts/default?alt=json&q=<日文關鍵字>&max-results=N

  回傳 JSON，每筆有標題（日文）、頁面網址、圖檔直連、標籤。
  robots.txt 是空的（0 bytes），沒有任何爬取限制。

🔴 授權（這條決定了這支程式的設計）
  いらすとや 的規定：
    「商用目的の場合、一つの作成物の中に20点までは無料でご利用いただけます。
      それ以上の点数をご希望される場合は有償となります。」
    【繁中】商業用途時，同一件作品中最多可免費使用 20 張；超過就要付費
            （每張 1,100 日圓含稅）。
    另有一條：**素材本身不可販售、不可再散布。**

  所以這支 server **絕對不下載、不快取、不夾帶任何圖檔**，
  只回傳「網址」，讓使用者自己去官網下載。
  每次搜尋也會附上剩餘可用張數的提醒。

  ⚠ 診所免費發送的衛教單算不算「商用」是灰色地帶。
    我不是律師。實務上的安全線：單張沒問題，成套（20 張以上）要留意。
"""
import io
import re
import sys
import json
import time
import urllib.parse
from typing import Any, Optional

import requests
from mcp.server.fastmcp import FastMCP

# Windows 終端機印日文/中文會炸，先把 stdout 換成 utf-8
if sys.platform == "win32":
    try:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
    except Exception:
        pass

BASE = "https://www.irasutoya.com"
FEED = BASE + "/feeds/posts/default"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 30

# 反爬紀律：序列不並發，每次請求之間喘一下。
# 這是免費資源，不要把人家打掛。
POLITE_DELAY = 0.8
_last_call = [0.0]

mcp = FastMCP("irasutoya")


# ── 中文 → 日文關鍵字對照 ────────────────────────────
# 這張表是這支 MCP 的靈魂。沒有它，心理師根本不知道要打什麼。
# 一個中文詞對到多個日文詞，是因為日文同一個概念常有漢字／假名／外來語三種寫法，
# 只打一種會漏掉一大半素材。
ZH_TO_JA: dict[str, list[str]] = {
    # ── 情緒 ──
    "情緒": ["感情", "気持ち", "表情"],
    "開心": ["笑顔", "喜ぶ", "嬉しい"],
    "快樂": ["笑顔", "喜ぶ"],
    "生氣": ["怒る", "怒り"],
    "難過": ["泣く", "悲しい", "落ち込む"],
    "哭": ["泣く"],
    "焦慮": ["不安", "心配"],
    "緊張": ["緊張", "不安"],
    "害怕": ["怖い", "恐怖"],
    "平靜": ["リラックス", "落ち着く"],
    "放鬆": ["リラックス", "深呼吸", "瞑想"],
    "深呼吸": ["深呼吸"],
    "壓力": ["ストレス", "疲れ"],
    "煩惱": ["悩み", "困る"],

    # ── 兒童與發展 ──
    "小孩": ["子供", "こども"],
    "孩子": ["子供", "こども"],
    "小朋友": ["子供", "こども"],
    "學生": ["学生", "小学生", "児童"],
    "小學生": ["小学生"],
    "兒童": ["子供", "児童"],
    "嬰兒": ["赤ちゃん", "乳児"],
    "青少年": ["中学生", "高校生", "思春期"],
    "發展遲緩": ["発達障害", "療育"],
    "學習障礙": ["勉強", "発達障害"],
    "閱讀": ["読書", "本を読む"],
    "讀字": ["読書", "音読"],
    "識字": ["読み書き", "文字"],
    "寫字": ["書く", "鉛筆", "文字"],
    "抄寫": ["書く", "ノート"],
    "計算": ["計算", "算数"],
    "記憶": ["記憶", "覚える", "忘れる"],
    "忘記": ["忘れる", "物忘れ"],
    "過動": ["落ち着きがない", "発達障害"],
    "注意力": ["集中", "気が散る"],
    "專心": ["集中", "勉強"],
    "分心": ["気が散る", "よそ見"],

    # ── 學校 ──
    "學校": ["学校", "教室"],
    "上課": ["授業", "教室"],
    "老師": ["先生", "教師"],
    "同學": ["友達", "クラスメイト"],
    "功課": ["宿題", "勉強"],
    "考試": ["テスト", "試験"],
    "書包": ["ランドセル", "カバン"],
    "聯絡簿": ["連絡帳", "ノート"],
    "霸凌": ["いじめ"],
    "拒學": ["不登校"],

    # ── 家庭 ──
    "家庭": ["家族", "家庭"],
    "親子": ["親子", "子育て"],
    "父母": ["両親", "父親", "母親"],
    "家長": ["両親", "保護者", "母親"],
    "照顧孩子": ["子育て", "育児"],
    "媽媽": ["母親", "ママ"],
    "爸爸": ["父親", "パパ"],
    "手足": ["兄弟", "姉妹"],
    "祖父母": ["祖父母", "おじいさん", "おばあさん"],
    "夫妻": ["夫婦"],
    "吵架": ["喧嘩", "口論"],
    "家事": ["家事", "掃除"],

    # ── 心理與助人 ──
    "諮商": ["カウンセリング", "相談"],
    "心理師": ["カウンセラー", "心理"],
    "會談": ["面談", "相談"],
    "傾聽": ["話を聞く", "相談"],
    "團體": ["グループ", "会議"],
    "社工": ["ソーシャルワーカー", "福祉"],
    "轉介": ["紹介", "相談"],
    "自我傷害": ["リストカット", "落ち込む"],
    "憂鬱": ["うつ病", "落ち込む"],
    "失眠": ["不眠", "眠れない"],
    "睡眠": ["睡眠", "寝る"],

    # ── 醫療 ──
    "醫院": ["病院", "クリニック"],
    "醫師": ["医者", "医師"],
    "護理師": ["看護師"],
    "看診": ["診察", "問診"],
    "吃藥": ["薬", "服薬"],
    "打針": ["注射", "予防接種"],
    "復健": ["リハビリ"],
    "身心障礙": ["障害者", "車椅子"],
    "輪椅": ["車椅子"],

    # ── 長照與高齡 ──
    "長照": ["介護", "高齢者"],
    "老人": ["高齢者", "おじいさん", "おばあさん"],
    "失智": ["認知症"],
    "照顧者": ["介護", "家族"],

    # ── 3C 與生活 ──
    "手機": ["スマホ", "スマートフォン"],
    "電腦": ["パソコン"],
    "電玩": ["ゲーム", "テレビゲーム"],
    "網路": ["インターネット", "ネット"],
    "運動": ["運動", "スポーツ"],
    "吃飯": ["食事", "ご飯"],
    "洗澡": ["お風呂"],
    "刷牙": ["歯磨き"],

    # ── 工作與職場 ──
    "工作": ["仕事", "会社"],
    "加班": ["残業"],
    "疲累": ["疲れ", "疲労"],
    "會議": ["会議", "打ち合わせ"],
    "簡報": ["プレゼン", "発表"],

    # ── 表格與文件（做衛教單常用的裝飾）──
    "表格": ["表", "チェックリスト"],
    "清單": ["チェックリスト", "リスト"],
    "日曆": ["カレンダー"],
    "時鐘": ["時計"],
    "箭頭": ["矢印"],
    "獎勵": ["ご褒美", "花丸", "メダル"],
    "加油": ["応援", "頑張る"],
}

# 助人者常用的主題群，給 list_topics 用
TOPIC_GROUPS: dict[str, list[str]] = {
    "情緒": ["情緒", "開心", "生氣", "難過", "焦慮", "平靜", "放鬆", "深呼吸", "壓力"],
    "兒童與發展": ["孩子", "小孩", "嬰兒", "青少年", "發展遲緩", "學習障礙",
                    "注意力", "專心", "分心", "記憶", "忘記"],
    "學校": ["學校", "上課", "老師", "同學", "功課", "考試", "聯絡簿",
              "閱讀", "讀字", "識字", "寫字", "抄寫", "計算", "霸凌", "拒學"],
    "家庭": ["家庭", "親子", "父母", "家長", "照顧孩子", "手足", "祖父母", "夫妻", "吵架"],
    "心理與助人": ["諮商", "心理師", "會談", "傾聽", "團體", "社工", "轉介", "憂鬱", "失眠", "睡眠"],
    "醫療": ["醫院", "醫師", "護理師", "看診", "吃藥", "復健", "身心障礙", "輪椅"],
    "長照與高齡": ["長照", "老人", "失智", "照顧者"],
    "生活與 3C": ["手機", "電玩", "網路", "運動", "吃飯", "睡眠", "刷牙"],
    "版面裝飾": ["表格", "清單", "日曆", "時鐘", "箭頭", "獎勵", "加油"],
}

LICENSE_LINE = (
    "📋 授權提醒：商用時同一件作品最多免費用 20 張，超過每張 1,100 日圓（含稅）。"
    "素材本身不可販售或再散布。一份衛教單用 1–3 張完全沒問題。"
)


# ── 底層 ─────────────────────────────────────────
def _polite_sleep() -> None:
    """免費資源，序列不並發，兩次請求之間喘一下。"""
    gap = time.time() - _last_call[0]
    if gap < POLITE_DELAY:
        time.sleep(POLITE_DELAY - gap)
    _last_call[0] = time.time()


def _fetch(params: dict) -> dict:
    _polite_sleep()
    r = requests.get(FEED, params=params, headers={"User-Agent": UA},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _first_image(content_html: str) -> Optional[str]:
    """從 entry 的 content HTML 抓第一張圖的直連網址。

    注意：這裡只抓「網址字串」，不下載任何檔案——
    いらすとや 禁止素材再散布，所以這支程式從頭到尾不碰圖檔本身。
    """
    m = re.search(r'src="([^"]+\.(?:png|jpg|jpeg|gif))"', content_html or "",
                  re.I)
    return m.group(1) if m else None


# 節慶／生肖類的素材會嚴重干擾搜尋 —— 實測「孩子」搜出「肩車をする龍の家族
# （辰年）」這種賀年圖，因為 Blogger 是全文比對不是語意比對，「子」在干支裡也出現。
# 做衛教單幾乎用不到這類，預設濾掉。
NOISE_LABELS = {"年賀状", "干支", "正月", "クリスマス", "ハロウィン",
                "バレンタイン", "ひな祭り", "こどもの日", "七夕", "節分"}
NOISE_WORDS = ("年賀", "干支", "子年", "丑年", "寅年", "卯年", "辰年", "巳年",
               "午年", "未年", "申年", "酉年", "戌年", "亥年", "クリスマス")


def _is_noise(row: dict) -> bool:
    if set(row.get("labels") or []) & NOISE_LABELS:
        return True
    t = row.get("title_ja") or ""
    return any(w in t for w in NOISE_WORDS)


def _parse_entries(data: dict) -> list[dict]:
    feed = data.get("feed") or {}
    out = []
    for e in feed.get("entry") or []:
        title = (e.get("title") or {}).get("$t", "")
        links = [l.get("href") for l in (e.get("link") or [])
                 if l.get("rel") == "alternate"]
        content = (e.get("content") or {}).get("$t", "")
        labels = [c.get("term") for c in (e.get("category") or [])]
        out.append({
            "title_ja": title,
            "page": links[0] if links else None,
            "image": _first_image(content),
            "labels": labels,
        })
    return out


def _search_one(kw_ja: str, limit: int,
                drop_noise: bool = True) -> list[dict]:
    # 多抓一些再濾，免得濾完不夠數
    data = _fetch({"alt": "json", "q": kw_ja,
                   "max-results": limit * 3 if drop_noise else limit})
    rows = _parse_entries(data)
    if drop_noise:
        kept = [r for r in rows if not _is_noise(r)]
        # 濾到一張不剩就退回原始結果 —— 有總比沒有好
        rows = kept or rows
    return rows[:limit]


def _expand(query: str) -> tuple[list[str], str]:
    """把中文查詢展開成一組日文關鍵字。

    回傳 (日文關鍵字清單, 說明用的一句話)。
    查不到對照就原樣送出去 —— 使用者可能本來就打日文。
    """
    q = query.strip()
    if q in ZH_TO_JA:
        return ZH_TO_JA[q], "「%s」展開成日文：%s" % (q, "、".join(ZH_TO_JA[q]))

    # 部分比對：「注意力不集中」對得到「注意力」
    hits: list[str] = []
    matched: list[str] = []
    for zh, jas in ZH_TO_JA.items():
        if zh in q:
            matched.append(zh)
            for j in jas:
                if j not in hits:
                    hits.append(j)
    if hits:
        return hits, "從「%s」認出 %s，展開成日文：%s" % (
            q, "／".join(matched), "、".join(hits))

    return [q], "沒有對照到中文詞，直接用「%s」去搜（若是日文就會有結果）" % q


def _fmt(rows: list[dict], note: str, used_hint: Optional[int] = None) -> str:
    if not rows:
        return note + "\n\n（沒有結果。換個說法試試，或用 list_topics 看有哪些現成主題。）"
    lines = [note, ""]
    for i, r in enumerate(rows, 1):
        lines.append("%d. %s" % (i, r["title_ja"]))
        if r.get("labels"):
            lines.append("   標籤：%s" % "、".join(r["labels"]))
        if r.get("image"):
            lines.append("   圖檔：%s" % r["image"])
        if r.get("page"):
            lines.append("   頁面：%s" % r["page"])
        lines.append("")
    lines.append("找到 %d 張。" % len(rows))
    if used_hint is not None:
        lines.append("這份作品目前用了 %d 張，免費額度還剩 %d 張。"
                     % (used_hint, max(0, 20 - used_hint)))
    lines.append(LICENSE_LINE)
    return "\n".join(lines)


# ── Tools：查詢層 ────────────────────────────────
@mcp.tool()
def search(query: str, limit: int = 8) -> str:
    """用中文（或日文）找插圖。

    這是最常用的一支。你打中文，它自動翻成日文關鍵字去搜，
    因為 いらすとや 只吃日文。

    Args:
        query: 想找的東西，中文即可。例如「注意力不集中」「親子吵架」「深呼吸」
        limit: 最多回幾張（預設 8）
    """
    kws, note = _expand(query)
    seen: set[str] = set()
    rows: list[dict] = []
    for kw in kws:
        if len(rows) >= limit:
            break
        try:
            for r in _search_one(kw, limit):
                key = r.get("page") or r.get("title_ja")
                if key in seen:
                    continue
                seen.add(key)
                rows.append(r)
                if len(rows) >= limit:
                    break
        except Exception as e:
            note += "\n（關鍵字「%s」查詢失敗：%s）" % (kw, e)
    return _fmt(rows, note)


@mcp.tool()
def search_ja(query_ja: str, limit: int = 8) -> str:
    """直接用日文關鍵字搜（你自己知道要打什麼的時候用）。

    Args:
        query_ja: 日文關鍵字，例如「深呼吸」「カウンセリング」
        limit: 最多回幾張
    """
    rows = _search_one(query_ja, limit)
    return _fmt(rows, "日文關鍵字：%s" % query_ja)


@mcp.tool()
def list_topics() -> str:
    """列出助人工作者常用的主題（中日對照）。

    不知道要找什麼的時候先看這個。
    """
    lines = ["いらすとや 常用主題（中文 → 日文關鍵字）", ""]
    for group, words in TOPIC_GROUPS.items():
        lines.append("【%s】" % group)
        for w in words:
            lines.append("   %-8s → %s" % (w, "、".join(ZH_TO_JA.get(w, []))))
        lines.append("")
    lines.append("共 %d 個中文詞可用。直接對 search 說中文就行。"
                 % len(ZH_TO_JA))
    return "\n".join(lines)


@mcp.tool()
def browse_label(label_ja: str, limit: int = 12) -> str:
    """依 いらすとや 自己的標籤瀏覽。

    標籤是日文，例如「医療機器」「学校」「家族」。
    搜尋結果裡每一張都會附標籤，看到喜歡的可以拿來繼續挖。

    Args:
        label_ja: 日文標籤
        limit: 最多回幾張
    """
    _polite_sleep()
    url = "%s/feeds/posts/default/-/%s" % (
        BASE, urllib.parse.quote(label_ja))
    r = requests.get(url, params={"alt": "json", "max-results": limit},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    rows = _parse_entries(r.json())
    return _fmt(rows, "標籤：%s" % label_ja)


# ── Tools：分析層（這層才是 MCP 真正的價值）────────
@mcp.tool()
def suggest_for_worksheet(situation: str, count: int = 5) -> str:
    """描述一個狀況，一次配好整份衛教單／學習單要用的插圖。

    這支跟 search 的差別，就是「查資料」跟「幫你想」的差別。
    你不用自己拆關鍵字，直接講狀況：

      「一個小三的孩子，讀字會跳行、抄聯絡簿抄不完，家長很焦慮」

    它會自己拆出「兒童／學習障礙／聯絡簿／焦慮／家長」這幾條線，
    每條線各配一張，湊成一份版面用得上的組合。

    ⚠ 你描述的是「狀況」，不是「個案」——
      沒有姓名、沒有病歷、沒有可辨識資訊，本來就是去識別化的。

    Args:
        situation: 狀況描述，中文
        count: 想配幾張（預設 5，建議不超過 8）
    """
    matched = [zh for zh in ZH_TO_JA if zh in situation]
    if not matched:
        return ("從這段描述裡沒認出可用的主題詞。\n"
                "試試更具體一點，或先用 list_topics 看有哪些詞。\n"
                "原描述：%s" % situation)

    # 每個認出來的主題各取一張，湊成一組，而不是同一個概念塞五張
    per = max(1, count // max(1, len(matched)))
    rows: list[dict] = []
    seen: set[str] = set()
    used_for: list[str] = []

    for zh in matched:
        if len(rows) >= count:
            break
        for kw in ZH_TO_JA[zh]:
            got = 0
            try:
                for r in _search_one(kw, per + 2):
                    key = r.get("page") or r.get("title_ja")
                    if key in seen:
                        continue
                    seen.add(key)
                    r["for_zh"] = zh
                    rows.append(r)
                    used_for.append(zh)
                    got += 1
                    if got >= per or len(rows) >= count:
                        break
            except Exception:
                continue
            if got:
                break

    note = ("狀況：%s\n從裡面認出這幾條線：%s\n每條線各配了圖，湊成一組："
            % (situation, "、".join(matched)))
    if not rows:
        return note + "\n\n（都沒搜到結果，可能是關鍵字太冷門。）"

    lines = [note, ""]
    for i, r in enumerate(rows, 1):
        lines.append("%d. [%s] %s" % (i, r.get("for_zh", ""), r["title_ja"]))
        if r.get("image"):
            lines.append("   圖檔：%s" % r["image"])
        if r.get("page"):
            lines.append("   頁面：%s" % r["page"])
        lines.append("")
    lines.append("配好 %d 張。" % len(rows))
    lines.append("這份作品用了 %d 張，免費額度還剩 %d 張。"
                 % (len(rows), max(0, 20 - len(rows))))
    lines.append(LICENSE_LINE)
    return "\n".join(lines)


@mcp.tool()
def license_check(count: int) -> str:
    """算一下用了幾張會不會超過免費額度。

    Args:
        count: 這份作品打算用幾張
    """
    if count <= 20:
        return ("用 %d 張 → **免費**。\n"
                "商用時同一件作品 20 張以內都不用錢，你還有 %d 張的空間。\n"
                "%s" % (count, 20 - count, LICENSE_LINE))
    fee = count * 1100
    return ("用 %d 張 → **要付費**。\n"
            "超過 20 張之後是全部計價，每張 1,100 日圓（含稅），\n"
            "%d 張 × 1,100 = **%s 日圓**。\n\n"
            "實務建議：一份衛教單控制在 1–3 張；"
            "要做成套教材的話，把它拆成幾份獨立的單張，"
            "或改用其他授權更寬鬆的圖庫。\n%s"
            % (count, count, format(fee, ","), LICENSE_LINE))


if __name__ == "__main__":
    mcp.run()
