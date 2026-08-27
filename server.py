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

    # ═══ 以下 65 詞為 2026-08-27 實際打過 API 驗證過的 ═══
    # 方法：候選詞全部拿去真的搜一次，搜不到的直接剔掉。
    # 「選擇性緘默」就是這樣被剔掉的——概念存在，但圖庫裡沒有對應素材。

    # ── 情緒（細緻版，諮商做情緒辨識用） ──
    "羞愧": ["恥ずかしい", "赤面"],
    "罪惡感": ["反省", "謝る"],
    "嫉妒": ["嫉妬"],
    "失落": ["落ち込む"],
    "孤單": ["孤独", "ひとりぼっち"],
    "寂寞": ["寂しい", "孤独"],
    "無助": ["途方に暮れる", "困る"],
    "委屈": ["泣く", "我慢"],
    "挫折": ["失敗", "落ち込む"],
    "後悔": ["後悔", "反省"],
    "期待": ["期待", "楽しみ"],
    "感謝": ["感謝", "ありがとう"],
    "驕傲": ["自慢", "得意"],
    "興奮": ["興奮", "喜ぶ"],
    "無聊": ["退屈", "暇"],
    "厭煩": ["うんざり", "面倒"],
    "驚訝": ["驚く", "びっくり"],
    "困惑": ["困る", "疑問"],
    "忍耐": ["我慢"],
    "情緒失控": ["癇癪"],
    "發脾氣": ["癇癪", "怒る"],
    "冷靜": ["落ち着く", "冷静"],

    # ── 助人者自己的處境 ──
    "耗竭": ["燃え尽き症候群", "疲れ"],
    "過勞": ["過労", "残業"],
    "督導": ["指導", "面談"],
    "會議討論": ["会議", "打ち合わせ"],
    "個案研討": ["会議"],
    "紀錄": ["書類", "記録"],
    "文書工作": ["書類", "事務作業"],
    "家訪": ["訪問", "家庭訪問"],
    "轉介單": ["書類", "紹介状"],
    "通報": ["通報", "電話"],
    "守門人": ["相談"],

    # ── 臨床議題 ──
    "創傷": ["トラウマ"],
    "恐慌": ["パニック", "過呼吸"],
    "強迫": ["潔癖", "手洗い"],
    "飲食障礙": ["拒食症", "過食"],
    "成癮": ["依存症", "アルコール"],
    "網路成癮": ["ゲーム依存", "スマホ依存"],
    "自殺": ["落ち込む"],
    "自傷": ["リストカット"],
    "虐待": ["虐待", "体罰"],
    "家暴": ["DV", "暴力"],
    "哀傷": ["お葬式", "悲しい"],
    "失親": ["お葬式", "遺影"],
    "離婚": ["離婚"],
    "婚姻衝突": ["夫婦喧嘩"],
    "親職壓力": ["育児疲れ", "子育て"],
    "社交焦慮": ["人見知り", "緊張"],

    # ── 介入與技巧 ──
    "正念": ["瞑想"],
    "肌肉放鬆": ["ストレッチ", "リラックス"],
    "情緒調節": ["深呼吸", "落ち着く"],
    "獎勵表": ["ご褒美", "シール"],
    "代幣": ["シール", "スタンプ"],
    "作息表": ["スケジュール", "カレンダー"],
    "溝通": ["会話", "話し合い"],
    "傾聽姿勢": ["話を聞く", "相談"],
    "遊戲治療": ["おもちゃ", "積み木"],
    "藝術治療": ["お絵かき", "絵を描く"],
    "沙盤": ["砂場", "箱庭"],
    "音樂治療": ["音楽", "楽器"],
    "團體活動": ["グループ", "輪になる"],
    "桌遊": ["ボードゲーム", "カードゲーム"],
    "繪本": ["絵本", "読み聞かせ"],

    # ── 其他 ──
    "電話": ["電話"],
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
    "情緒（細緻）": ["羞愧", "罪惡感", "嫉妒", "失落", "孤單", "無助", "委屈",
                     "挫折", "後悔", "期待", "感謝", "興奮", "無聊", "驚訝",
                     "情緒失控", "發脾氣", "冷靜", "忍耐"],
    "助人者自己": ["耗竭", "過勞", "督導", "個案研討", "紀錄", "文書工作",
                   "家訪", "轉介單", "通報", "守門人"],
    "臨床議題": ["創傷", "恐慌", "強迫", "飲食障礙", "成癮", "網路成癮",
                 "自傷", "虐待", "家暴", "哀傷", "失親", "離婚", "婚姻衝突",
                 "親職壓力", "社交焦慮"],
    "介入與技巧": ["正念", "肌肉放鬆", "情緒調節", "獎勵表", "代幣", "作息表",
                   "溝通", "傾聽姿勢", "遊戲治療", "藝術治療", "沙盤",
                   "音樂治療", "團體活動", "桌遊", "繪本"],
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


# ── Tools：嵌入層（Artifact 的 CSP 擋外部圖，這層是為了那個）──
@mcp.tool()
def inline_images(html_path: str, size: str = "s400",
                  max_images: int = 20) -> str:
    """把 HTML 檔裡所有 いらすとや 的圖，就地換成內嵌的 data: URI。

    🔴 為什麼需要這支：
      Artifact 有嚴格的 CSP，會擋掉所有外部主機的圖片
      （唯一例外是 Google Fonts）。いらすとや 的圖放在
      blogger.googleusercontent.com，直接連一定載不出來。

    怎麼用（這個順序是刻意的）：
      1. 先用 search / suggest_for_worksheet 找圖，拿到網址
      2. 正常寫你的衛教單 HTML，img 就用那些**真實網址**
         —— 這樣檔案是人看得懂、改得動的
      3. 最後叫這支，把檔案裡的圖全部換成內嵌
      4. 發布成 Artifact

    這樣做的好處是那幾十萬個 base64 字元**從頭到尾不會進到對話裡**，
    只在檔案裡。對話乾淨，檔案自包含。

    Args:
        html_path: 你的 HTML 檔路徑
        size: 要抓多大。s200 螢幕看夠用、s400 印得出來（預設）、s640 很大
        max_images: 最多處理幾張。預設 20 —— 這個數字就是授權的免費上限
    """
    import os
    import base64

    if not os.path.isfile(html_path):
        return "找不到檔案：%s" % html_path
    if not html_path.lower().endswith((".html", ".htm")):
        return "這支只處理 .html / .htm，收到的是：%s" % html_path

    html = open(html_path, encoding="utf-8").read()

    # 只抓 いらすとや 用的那個 CDN，不碰檔案裡其他的圖
    pat = re.compile(
        r'src\s*=\s*"(https://blogger\.googleusercontent\.com/[^"]+)"')
    urls: list[str] = []
    for m in pat.finditer(html):
        u = m.group(1)
        if u not in urls:
            urls.append(u)

    if not urls:
        return ("這個檔案裡沒有 いらすとや 的圖。\n"
                "（只會處理 blogger.googleusercontent.com 的圖，"
                "其他來源不碰。）")

    over = len(urls) > max_images
    todo = urls[:max_images]

    done, failed, total_bytes = 0, [], 0
    for u in todo:
        # 換尺寸：網址裡的 /sNNN/ 是 Blogger 的縮圖參數
        target = re.sub(r'/s\d+/', '/%s/' % size, u)
        try:
            _polite_sleep()
            r = requests.get(target, headers={"User-Agent": UA},
                             timeout=TIMEOUT)
            r.raise_for_status()
            raw = r.content
        except Exception as e:
            failed.append("%s（%s）" % (u.rsplit("/", 1)[-1], e))
            continue

        ext = (u.rsplit(".", 1)[-1] or "png").lower().split("?")[0]
        mime = {"png": "image/png", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "gif": "image/gif"}.get(ext, "image/png")
        b64 = base64.b64encode(raw).decode("ascii")
        data_uri = "data:%s;base64,%s" % (mime, b64)

        html = html.replace('"%s"' % u, '"%s"' % data_uri)
        done += 1
        total_bytes += len(b64)

    open(html_path, "w", encoding="utf-8").write(html)

    lines = ["內嵌完成：%s" % os.path.basename(html_path), ""]
    lines.append("  換掉 %d 張圖（尺寸 %s）" % (done, size))
    lines.append("  內嵌後增加約 %.1f KB" % (total_bytes / 1024))
    if failed:
        lines.append("  ⚠ 有 %d 張抓不下來：" % len(failed))
        for f in failed:
            lines.append("     %s" % f)
    if over:
        lines.append("")
        lines.append("  ⚠ 檔案裡有 %d 張，只處理了前 %d 張。"
                     % (len(urls), max_images))
        lines.append("     20 張是授權的免費上限，超過要付費——"
                     "這不是技術限制，是刻意擋在這裡的。")
    lines.append("")
    lines.append("  現在這個檔案是自包含的，可以直接發布成 Artifact。")
    lines.append("  %s" % LICENSE_LINE)
    return "\n".join(lines)


# ── Tools：圖庫本身的統計與分類 ──────────────────
@mcp.tool()
def library_stats() -> str:
    """這個圖庫有多大、多新、有幾個分類。"""
    _polite_sleep()
    r = requests.get(BASE + "/feeds/posts/summary",
                     params={"alt": "json", "max-results": 0},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    f = r.json().get("feed") or {}
    total = (f.get("openSearch$totalResults") or {}).get("$t", "?")
    updated = (f.get("updated") or {}).get("$t", "?")
    title = (f.get("title") or {}).get("$t", "")
    cats = [c.get("term") for c in (f.get("category") or [])]
    return "\n".join([
        "いらすとや 圖庫現況",
        "",
        "  站名      %s（可愛的免費素材集）" % title,
        "  素材總數  %s 張" % total,
        "  分類數    %d 個" % len(cats),
        "  最後更新  %s" % updated[:19].replace("T", " "),
        "",
        "  網址      %s" % BASE,
        "  %s" % LICENSE_LINE,
    ])


# 239 個分類裡，助人工作者用得到的那些（中譯是為了你，站上只有日文）
LABEL_ZH: dict[str, str] = {
    "こども": "兒童", "あかちゃん": "嬰兒", "若者": "年輕人",
    "中年": "中年", "老人": "老人", "世代": "世代",
    "家族": "家族", "友達": "朋友", "恋愛": "戀愛",
    "学校": "學校", "幼稚園": "幼稚園", "受験": "應試",
    "新学期": "新學期", "入学式": "開學典禮", "卒業式": "畢業典禮",
    "給食": "營養午餐", "運動会": "運動會", "文化祭": "文化祭",
    "体育": "體育", "こども職業": "兒童職業",
    "医療": "醫療", "医療機器": "醫療器材", "病気": "生病",
    "怪我": "受傷", "健康診断": "健康檢查", "人体": "人體",
    "歯": "牙齒", "マスク": "口罩", "介護": "照護",
    "お葬式": "喪禮", "メタボリック": "代謝症候群",
    "表情": "表情", "ポーズ": "姿勢", "棒人間": "火柴人",
    "顔アイコン": "表情圖示", "似顔絵": "人像畫",
    "睡眠": "睡眠", "生活": "生活", "食事": "飲食",
    "お風呂": "洗澡", "掃除": "打掃", "洗濯": "洗衣",
    "トイレ": "廁所", "マナー": "禮儀", "あいさつ": "問候",
    "運動": "運動", "スポーツ": "運動項目", "ヨガ": "瑜伽", "ダンス": "舞蹈",
    "インターネット": "網路", "スマートフォン": "智慧型手機",
    "コンピューター": "電腦", "ライン": "LINE通訊",
    "書類": "文件", "文房具": "文具", "本": "書籍",
    "テンプレート": "版面範本", "フレーム": "外框",
    "メッセージ": "訊息", "メッセージカード": "訊息卡",
    "伝言メモ": "留言備忘", "一筆箋": "便條紙",
    "パターン": "圖樣", "飾り": "裝飾", "マーク": "符號",
    "POP": "POP海報字", "書体": "字體",
    "災害": "災害", "事故": "事故", "違反": "違規",
    "環境問題": "環境問題", "戦争": "戰爭",
    "LGBT": "LGBT", "ウェディング": "婚禮", "引越し": "搬家",
    "就活": "求職", "新社会人": "社會新鮮人", "会社": "公司",
    "ビジネス": "商務", "お金": "金錢", "職業": "職業",
    "おもちゃ": "玩具", "ボードゲーム": "桌遊",
    "音楽": "音樂", "楽器": "樂器", "美術": "美術", "物語": "故事",
}


@mcp.tool()
def list_labels(helper_only: bool = True) -> str:
    """列出圖庫的分類（標籤）。

    分類是 いらすとや 自己編的，全部是日文。
    用 browse_label 可以直接依分類瀏覽。

    Args:
        helper_only: True（預設）只列助人工作者用得到的、附中譯；
                     False 列出全部 239 個
    """
    _polite_sleep()
    r = requests.get(BASE + "/feeds/posts/summary",
                     params={"alt": "json", "max-results": 0},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    cats = [c.get("term")
            for c in ((r.json().get("feed") or {}).get("category") or [])]

    if not helper_only:
        lines = ["全部 %d 個分類（日文）：" % len(cats), ""]
        for i in range(0, len(cats), 5):
            lines.append("  " + "  ".join("%-14s" % c for c in cats[i:i + 5]))
        lines.append("")
        lines.append("用 browse_label(\"分類名\") 直接瀏覽。")
        return "\n".join(lines)

    groups = {
        "人與家庭": ["こども", "あかちゃん", "若者", "中年", "老人",
                     "家族", "友達", "世代", "恋愛", "ウェディング"],
        "學校": ["学校", "幼稚園", "受験", "新学期", "入学式", "卒業式",
                 "給食", "運動会", "文化祭", "体育", "こども職業"],
        "醫療與照護": ["医療", "医療機器", "病気", "怪我", "健康診断",
                       "人体", "歯", "マスク", "介護", "お葬式"],
        "情緒與人物": ["表情", "ポーズ", "棒人間", "顔アイコン", "似顔絵"],
        "生活作息": ["睡眠", "生活", "食事", "お風呂", "掃除", "洗濯",
                     "トイレ", "マナー", "あいさつ"],
        "身體活動": ["運動", "スポーツ", "ヨガ", "ダンス"],
        "3C 與通訊": ["インターネット", "スマートフォン",
                      "コンピューター", "ライン"],
        "文書與版面": ["書類", "文房具", "本", "テンプレート", "フレーム",
                       "メッセージ", "メッセージカード", "伝言メモ",
                       "一筆箋", "パターン", "飾り", "マーク", "POP", "書体"],
        "社會議題": ["災害", "事故", "違反", "環境問題", "戦争", "LGBT"],
        "工作": ["就活", "新社会人", "会社", "ビジネス", "お金", "職業"],
        "遊戲與藝術": ["おもちゃ", "音楽", "楽器", "美術", "物語"],
    }
    lines = ["助人工作者用得到的分類（共 %d 個站上分類，這裡挑出常用的）" % len(cats),
             ""]
    live = set(cats)
    for g, items in groups.items():
        avail = [x for x in items if x in live]
        if not avail:
            continue
        lines.append("【%s】" % g)
        for x in avail:
            lines.append("   %-16s %s" % (x, LABEL_ZH.get(x, "")))
        lines.append("")
    lines.append("用 browse_label(\"分類名\") 直接瀏覽，例如 browse_label(\"棒人間\")。")
    lines.append("要看全部 239 個：list_labels(helper_only=False)")
    return "\n".join(lines)


# ── Tools：產出層（衛教單的終點是印出來，不是螢幕）──
WORKSHEET_HTML = """<title>%%TITLE%%</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
/* ── 這份骨架同時要滿足兩件事：線上分享看得舒服、按 Ctrl+P 印出來能用 ──
   踩過的坑都寫在對應的地方。 */
:root{
  --ink:#1a1a1a; --ink-2:#444; --ink-3:#767676;
  --line:#d8d8d8; --accent:%%ACCENT%%; --accent-soft:%%ACCENT_SOFT%%;
  --paper:#ffffff;
}
*{box-sizing:border-box}
body{
  margin:0; background:#eceae5; color:var(--ink);
  font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;
  line-height:1.85; font-size:16px;
}
/* A4 內文區。螢幕上像一張紙，列印時就是那張紙。 */
.sheet{
  /* 寫死 210mm 的話手機會爆版（橫向捲動），所以取兩者較小值。
     列印時 @media print 會把它還原成真正的 A4。 */
  width:min(210mm, 100%); min-height:297mm; margin:1.5rem auto;
  padding:16mm 15mm;
  background:var(--paper); box-shadow:0 2px 18px rgba(0,0,0,.12);
}
header{border-bottom:3px solid var(--accent); padding-bottom:.8rem; margin-bottom:1.4rem}
.eyebrow{font-size:.8rem; letter-spacing:.14em; color:var(--accent);
  font-weight:700; margin:0 0 .35rem}
h1{font-size:1.7rem; line-height:1.35; margin:0; letter-spacing:-.01em}
.forwho{font-size:.9rem; color:var(--ink-3); margin:.5rem 0 0}
h2{font-size:1.15rem; margin:1.8rem 0 .7rem; padding-left:.6rem;
  border-left:4px solid var(--accent)}
p{margin:0 0 .8rem; max-width:38em}
ul,ol{margin:0 0 1rem; padding-left:1.4rem}
li{margin-bottom:.45rem}
/* 圖文並排。圖是輔助，不要喧賓奪主。 */
.row{display:flex; gap:1.2rem; align-items:flex-start; margin:1rem 0;
  flex-wrap:wrap}
.row img{width:110px; flex:0 0 110px; height:auto}
.row .txt{flex:1 1 14rem; min-width:0}
.box{background:var(--accent-soft); border-left:4px solid var(--accent);
  padding:1rem 1.2rem; margin:1.2rem 0; border-radius:0 3px 3px 0}
.box p:last-child{margin-bottom:0}
.check{list-style:none; padding:0}
.check li{padding-left:2rem; position:relative; margin-bottom:.7rem}
.check li::before{content:""; position:absolute; left:0; top:.35em;
  width:1.1rem; height:1.1rem; border:2px solid var(--ink-3); border-radius:2px}
footer{margin-top:2rem; padding-top:.9rem; border-top:1px solid var(--line);
  font-size:.78rem; color:var(--ink-3); line-height:1.7}
.bar{
  position:sticky; top:0; z-index:10; background:#fff; border-bottom:1px solid var(--line);
  padding:.6rem 1rem; display:flex; gap:.7rem; align-items:center; justify-content:center;
  font-size:.85rem;
}
.bar button{
  font:inherit; font-weight:700; padding:.4rem 1rem; border:1px solid var(--accent);
  background:var(--accent); color:#fff; border-radius:3px; cursor:pointer;
}
.bar span{color:var(--ink-3)}
.bar kbd{
  font-family:ui-monospace,Consolas,monospace; font-size:.78em;
  background:#f0efec; border:1px solid var(--line); border-bottom-width:2px;
  border-radius:3px; padding:.05rem .3rem;
}
.bar-note{color:var(--accent); font-weight:700; max-width:30rem; line-height:1.5}

/* ── 手機（個案很可能就是在手機上看這一份）──
   桌機的 16mm 邊界在 5 吋螢幕上等於整頁只剩中間一條，
   所以窄螢幕改用相對邊界，圖也放大置中，不要縮在角落。 */
@media (max-width:600px){
  body{background:#fff; font-size:17px}
  .sheet{margin:0; padding:1.2rem 1.1rem 2rem; box-shadow:none; min-height:0}
  h1{font-size:1.42rem}
  h2{font-size:1.08rem; margin-top:1.5rem}
  .row{gap:.9rem}
  .row img{width:82px; flex:0 0 82px}
  .box{padding:.9rem 1rem}
  .bar{font-size:.8rem; padding:.5rem .7rem}
  .bar{flex-wrap:wrap}
  .bar .kbd-hint{display:none}   /* 手機沒有 Ctrl+P，講了只會混淆 */
}
/* 更窄（或圖需要看清楚時）：圖獨立一行、放大置中 */
@media (max-width:400px){
  .row{flex-direction:column; align-items:center; text-align:left}
  .row img{width:132px; flex:none; align-self:center}
}

/* ── 列印 ──
   坑一：不加 @page，瀏覽器預設邊界會把版面擠掉。
   坑二：sticky 工具列會跟著印出來。
   坑三：圖被切在兩頁中間。
   坑四：深色底在雷射印表機上吃碳粉、家長看不清楚。 */
@page{ size:A4; margin:14mm; }
@media print{
  body{background:#fff; font-size:12pt}
  .sheet{width:auto; max-width:none; min-height:0; margin:0; padding:0;
          box-shadow:none}
  .bar{display:none}
  .row, .box, h2, img, li{break-inside:avoid; page-break-inside:avoid}
  h2{break-after:avoid; page-break-after:avoid}
  a{color:inherit; text-decoration:none}
  /* 網址印出來，家長看紙本才知道去哪 */
  footer a::after{content:" (" attr(href) ")"; font-size:.9em; color:#666}
}
</style>

<div class="bar">
  <button type="button" id="btnPrint">列印這一份</button>
  <span class="kbd-hint">或直接按 <kbd>Ctrl</kbd>+<kbd>P</kbd>（Mac：<kbd>⌘</kbd>+<kbd>P</kbd>）</span>
  <span class="bar-note" id="printNote" hidden></span>
</div>
<script>
/* 🔴 這顆按鈕在 Artifact 頁面上會「安靜地失效」。
   原因：Artifact 跑在 sandbox 的 iframe 裡，腳本觸發的 window.print()
   會被擋掉，而且**不會拋錯**——按下去什麼都不發生，使用者只會以為壞了。
   使用者自己按 Ctrl+P 是瀏覽器原生行為，擋不住，所以那條路一定通。
   這裡的作法：照樣試 print()，然後不管成不成功都把備案講清楚。 */
(function(){
  var b = document.getElementById('btnPrint');
  var note = document.getElementById('printNote');
  if(!b) return;
  var inFrame = (function(){ try { return window.self !== window.top; } catch(e){ return true; } })();
  b.addEventListener('click', function(){
    var ok = false;
    try { window.print(); ok = true; } catch(e){}
    if (!note) return;
    note.hidden = false;
    note.textContent = inFrame
      ? '沒跳出列印視窗？這一頁嵌在框架裡，瀏覽器會擋掉按鈕觸發的列印——請直接按 Ctrl+P。'
      : (ok ? '' : '瀏覽器擋掉了，請直接按 Ctrl+P。');
    if (!note.textContent) note.hidden = true;
  });
})();
</script>

<div class="sheet">
  <header>
    <p class="eyebrow">%%EYEBROW%%</p>
    <h1>%%TITLE%%</h1>
    <p class="forwho">%%FORWHO%%</p>
  </header>

  <!-- ↓↓↓ 以下是範例結構，直接改成你的內容 ↓↓↓ -->

  <h2>這是怎麼回事</h2>
  <div class="row">
    <img src="把 search 找到的圖檔網址貼在這裡" alt="">
    <div class="txt">
      <p>用家長聽得懂的話講一遍。避免專業術語，一句話講一件事。</p>
    </div>
  </div>

  <h2>在家可以做的三件事</h2>
  <ol>
    <li>具體到「今天晚上就能做」的程度。</li>
    <li>不要寫「多陪伴」這種做不出動作的話。</li>
    <li>三件就好。寫七件等於沒寫。</li>
  </ol>

  <div class="box">
    <p><b>什麼時候要回來找我們</b></p>
    <p>寫清楚具體的訊號，不要只說「情況變嚴重」。</p>
  </div>

  <h2>這週可以觀察看看</h2>
  <ul class="check">
    <li>可以打勾的觀察項目</li>
    <li>讓家長有事情做，而不是只有擔心</li>
  </ul>

  <!-- ↑↑↑ 以上是範例結構 ↑↑↑ -->

  <footer>
    %%FOOTER%%<br>
    插圖來源：<a href="https://www.irasutoya.com">いらすとや</a>（免費素材，本份使用數量在授權範圍內）
  </footer>
</div>
"""


@mcp.tool()
def worksheet_template(out_path: str,
                       title: str = "給家長的說明",
                       eyebrow: str = "衛教單",
                       forwho: str = "適用對象：（寫清楚這份是給誰的）",
                       footer: str = "如有疑問請與我們聯繫。",
                       accent: str = "#0e5c52") -> str:
    """產生一份「可列印 A4 ＋ 可線上分享」的衛教單／學習單骨架。

    衛教單的終點是印出來給家長，不是留在螢幕上。
    一般生出來的 HTML 直接列印會很慘，這份骨架先把坑填掉了：

      · @page 設 A4 與邊界（不設的話版面會被擠掉）
      · 列印時自動隱藏「列印」按鈕那一列
      · 圖片與段落 break-inside:avoid（不會被切在兩頁中間）
      · 列印時轉成白底（深色底吃碳粉，家長也看不清楚）
      · 頁尾的連結列印時會把網址一起印出來（紙本才知道去哪）
      · 「列印」按鈕在 Artifact 裡會被 sandbox 擋掉（而且不報錯），
        所以骨架裡常駐一行 Ctrl+P 的提示，按下去也會再講一次

    產生之後的流程：
      1. 這支產生骨架
      2. 你把內容改成這個孩子的狀況
      3. 用 search 找圖，img 的 src 先貼**真實網址**
      4. 叫 inline_images 把圖就地內嵌（Artifact 的 CSP 擋外部圖）
      5. 發布成 Artifact → 有網址可以傳給家長，家長也可以直接列印

    Args:
        out_path: 要存成哪個檔（.html）
        title: 大標題
        eyebrow: 標題上方的小字
        forwho: 適用對象
        footer: 頁尾說明
        accent: 主色，預設松綠 #0e5c52
    """
    import os

    if not out_path.lower().endswith((".html", ".htm")):
        out_path += ".html"
    if os.path.exists(out_path):
        return ("這個檔案已經存在：%s\n"
                "換一個檔名，或先確認舊的不要了再自己刪掉。"
                "（這支不覆寫既有檔案。）" % out_path)

    # 主色配一個很淡的底色
    def soft(hexcol: str) -> str:
        try:
            h = hexcol.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            r, g, b = (int(c + (255 - c) * 0.90) for c in (r, g, b))
            return "#%02x%02x%02x" % (r, g, b)
        except Exception:
            return "#eef4f2"

    # 用替換而不是 .format() —— 模板裡的 CSS/JS 有大量 { }，
    # 用 .format() 就得全部寫成 {{ }}，加一段新的 JS 忘了跳脫就會整支炸掉。
    html = WORKSHEET_HTML
    for k, v in (("%%TITLE%%", title), ("%%EYEBROW%%", eyebrow),
                 ("%%FORWHO%%", forwho), ("%%FOOTER%%", footer),
                 ("%%ACCENT%%", accent), ("%%ACCENT_SOFT%%", soft(accent))):
        html = html.replace(k, v)

    d = os.path.dirname(os.path.abspath(out_path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    open(out_path, "w", encoding="utf-8").write(html)

    return "\n".join([
        "骨架已產生：%s" % out_path,
        "",
        "  版面    A4（210×297mm），邊界 14mm",
        "  已處理  列印時隱藏工具列／圖不跨頁／轉白底／連結印出網址",
        "",
        "  接下來：",
        "   1. 把裡面的範例段落改成這個孩子的狀況",
        "   2. 用 search 或 suggest_for_worksheet 找圖，把網址貼進 img 的 src",
        "   3. 叫 inline_images(\"%s\") 把圖內嵌" % out_path,
        "   4. 發布成 Artifact —— 有網址可以傳，家長也可以直接印",
        "",
        "  ⚠ 寫內容時記得：你描述的是「狀況」，不是「個案」。",
        "    不要放姓名、生日、學校、病歷號這類可辨識資訊。",
    ])


if __name__ == "__main__":
    mcp.run()
