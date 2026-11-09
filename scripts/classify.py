"""Classify every post (topics, tickers, value, report type) -> kb/classification.json

Topics are scored from keyword hits in the title (x4), body, attachment text and transcripts.
kb/overrides.json ({id: {"topics": [...], "value": "..."}}) wins over the automatic result.

Usage: python scripts/classify.py
"""
import json
import re
from collections import Counter
from pathlib import Path

import pymupdf

from common import RAW, RAW_MEDIA, ROOT, all_post_ids, category_of, load_post

KB = ROOT / "kb"

TOPICS = {
    "宏觀與聯儲局": ["FOMC", "聯儲局", "Fed", "利率", "減息", "加息", "CPI", "PCE", "通脹", "債息", "yield", "非農", "就業",
                 "GDP", "衰退", "recession", "流動性", "liquidity", "美元", "DXY", "國債", "央行", "QE", "QT", "鮑威爾", "Powell",
                 "財政", "關稅", "tariff", "油價", "黃金"],
    "市場結構與情緒": ["市寬", "市場寬度", "breadth", "情緒", "sentiment", "AAII", "VIX", "put/call", "季節性", "seasonality",
                  "positioning", "CTA", "資金流", "fund flow", "COT", "持倉", "margin debt", "新高新低", "advance-decline",
                  "Market Monitor", "相關係數", "correlation", "牛市", "熊市", "調整", "correction"],
    "技術分析": ["RSI", "MACD", "移動平均", "均線", "天線", "突破", "breakout", "VCP", "形態", "支持位", "阻力", "K線", "陰陽燭",
             "candlestick", "Fibonacci", "trendline", "趨勢線", "成交量", "volume", "Stage", "Weinstein", "price action",
             "ATR", "VWAP", "Volume Profile", "Stochastic", "背馳", "divergence", "整固", "consolidation", "pullback", "回調",
             "chart", "圖表", "base"],
    "期權": ["期權", "option", "Put", "Call", "gamma", "引伸波幅", "implied volatility", "expected move", "0DTE", "covered call",
           "One Leg", "Long Call", "Long Put", "spread", "IV"],
    "選股與強勢股": ["強勢股", "選股", "relative strength", "相對強度", "RS ", "RS線", "RS line", "leader", "領導股", "龍頭", "動力股",
                 "動力交易", "momentum", "動能", "板塊", "sector", "輪動", "rotation", "episodic pivot", "EP ", "業績", "earnings",
                 "掃描", "screen", "watchlist", "觀察名單", "Minervini", "Qullamaggie", "CANSLIM", "O'Neil", "強勢", "主升浪",
                 "swing", "高增長", "growth"],
    "投資組合與資金管理": ["倉位", "position size", "position sizing", "資金管理", "money management", "投資組合", "portfolio",
                     "配置", "allocation", "對沖", "hedge", "R 原則", "1R", "risk per trade", "加倉", "減倉", "pyramid",
                     "equity curve", "分散", "diversif", "現金", "cash", "rebalanc", "槓桿", "leverage", "Kelly"],
    "風險管理": ["止損", "止蝕", "stop loss", "drawdown", "回撤", "風險管理", "risk management", "移動止損", "trailing stop",
             "risk/reward", "獎賠比", "風險回報", "R multiple", "max loss"],
    "交易心理": ["心理", "心態", "紀律", "discipline", "FOMO", "貪婪", "恐懼", "loss aversion", "偏誤", "bias", "耐性", "patience",
             "情緒管理", "自信", "後悔", "壓力", "運氣"],
    "統計與數據": ["統計", "回測", "backtest", "勝率", "win rate", "expectancy", "期望值", "機率", "probability", "base rate",
              "Monte Carlo", "歷史數據", "樣本", "sample size", "t-value", "標準差", "standard deviation", "平均回報"],
    "主題投資": ["AI", "人工智能", "半導體", "semiconductor", "晶片", "NVDA", "Nvidia", "數據中心", "data center", "光子", "矽光子",
             "記憶體", "memory", "生物科技", "biotech", "軍工", "太空", "SpaceX", "加密", "bitcoin", "比特幣", "OpenAI", "SaaS",
             "量子", "核能", "能源"],
    "公告": ["公告", "通告", "客服", "Patreon 平台", "Live Chat", "停止運作", "更新通告", "會員須知"],
}
TOPIC_ORDER = list(TOPICS)
MAX_TOPICS = 4

REPORT_TYPES = [
    ("COT 報告", r"COT"),
    ("Market Monitor", r"Market Monitor"),
    ("美元相關係數", r"Correlati|相關係數"),
    ("Weekly Expected Move", r"(?i)expected\s*move"),
    ("財經日曆", r"經濟數據與央行事件|財經日曆"),
    ("每週總結", r"每週總結"),
    ("每日更新", r"每日更新|每日更新"),
    ("市場消息焦點", r"市場消息焦點|新聞消息摘要"),
    ("Podcast 筆記", r"(?i)podcast|All[- ]In"),
]

NOT_TICKERS = set("""
AI ATR RSI MACD FOMC CPI PCE GDP USD HKD EUR JPY CNY ETF ETFS IPO CEO CFO CTO VIX COT CTA QE QT FED SEC IV EP RS EPS PE PDF
TXT US USA UK EU HK SP OK AM PM YOY QOQ MOM SD OI DTE ATH ATL VWAP VCP TRR BMS MS API GPU CPU TPU HBM DRAM NAND LLM SAAS
NFP PPI ISM PMI ECB BOJ BOE PBOC IMF OPEC WTI EIA ADP JOLTS FOMO TA FA DCA YTD MTD R RR NYSE NASDAQ CBOE CME LIVE NEW
THE AND FOR WITH FROM THAT THIS NOT YOU ARE BUT CUP SIR HAMMER PRO TIER BTC ETH DXY TLT HYG LQD IG HY CDS MBS QQQ SPY IWM
DIA MDY SPX NDX RUT VOO VTI DJIA KRE XLF XLK XLE XLV XLY XLP XLI XLB XLU XLRE XLC SMH SOXX SOX ARKK GLD SLV USO UNG FX
SI ABC CC CN JP KR TW DB AGI ASI ESG OTC MOU ROI ROE ROIC FCF EBIT EBITDA TAM SAM OEM ODM CAPEX OPEX GAAP KPI AUM NAV
OIL AUD GBP CAD CHF NZD MXN CL YM ZT ZF ZN ZB COCOA KC HG CORN ZC EST EDT ET PT IBKR CA EMA SMA SFC CNBC AU GW AWS AP
WSJ FT NYT CNN BBC DOJ FTC FDA NIH DOD DOE NATO WHO GOP CBO BLS BEA LME ICE ASX TSX HKEX SGX LSE JPX KOSPI
NO YES OR IF IN ON AT TO OF BY AN AS IS IT BE WE MY UP SO DO GO VS PS NB FAQ PDF PNG Q1 Q2 Q3 Q4 H1 H2 FY CY
CPO ASIC GPT NASA ATM USDC USDT ARR MA SOLS MRD LLC INC LTD PLC NVL CUDA CXL PCIE TSMC
""".split())
DISCLAIMER = re.compile(r"(免責聲明|利益申報|不構成任何投資|並非投資建議|證券價格可升可跌|持牌|自行小心求證)[^\n]{0,400}")
INDEX_TICKERS = {"QQQ", "SPY", "IWM", "DIA", "MDY", "SPX", "NDX", "SMH", "SOXX", "TLT", "GLD", "BTC", "XLF", "XLK", "XLE", "KRE", "ARKK"}


def doc_text(attrs):
    s = attrs.get("content_json_string")
    if s:
        return " ".join(re.findall(r'"text":\s*"((?:[^"\\]|\\.)*)"', s))
    return re.sub(r"<[^>]+>", " ", attrs.get("content") or "")


def attachment_text(post_id, limit=15000):
    out = []
    folder = RAW_MEDIA / post_id
    if not folder.exists():
        return ""
    for f in sorted(folder.glob("*.pdf")):
        try:
            with pymupdf.open(f) as doc:
                out.append("".join(p.get_text() for p in doc)[:limit])
        except Exception:
            pass
    for f in sorted(folder.glob("*.txt")):
        out.append(f.read_text(errors="replace")[:2000])
    return "\n".join(out)


def transcript_text(post_id, limit=20000):
    p = RAW / "transcripts" / f"{post_id}.json"
    if not p.exists():
        return ""
    items = json.loads(p.read_text())
    return " ".join(sg["text"] for it in items for sg in (it.get("segments") or []))[:limit]


def count(text, kw):
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9/' -]*", kw.strip()) and len(kw.strip()) <= 4:
        return len(re.findall(rf"(?<![A-Za-z]){re.escape(kw.strip())}(?![A-Za-z])", text))
    return text.lower().count(kw.lower())


def score_topics(title, body):
    scores = {}
    for topic, kws in TOPICS.items():
        s = sum(count(title, k) * 4 + min(count(body, k), 15) for k in kws)
        if s:
            scores[topic] = s
    return scores


def pick_topics(scores, body_len):
    if not scores:
        return []
    threshold = max(4, min(25, body_len // 1500))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top = ranked[0][1]
    picked = [t for t, s in ranked if s >= threshold and s >= top * 0.3][:MAX_TOPICS]
    return picked or [ranked[0][0]]


def tickers(title, body):
    found = Counter()
    for m in re.findall(r"\$([A-Z]{1,5})\b", title + " " + body):
        found[m] += 3
    for m in re.findall(r"(?<![A-Za-z0-9$])([A-Z]{2,5})(?![A-Za-z0-9])", title + " " + body):
        if m not in NOT_TICKERS:
            found[m] += 1
    for m in re.findall(r"(?<![A-Za-z0-9$])([A-Z]{2,5})(?![A-Za-z0-9])", title):
        if m not in NOT_TICKERS:
            found[m] += 2
    keep = [t for t, n in found.most_common() if n >= 3 or (t in INDEX_TICKERS and n >= 2)]
    return keep[:15]


def report_type(title):
    for name, pat in REPORT_TYPES:
        if re.search(pat, title):
            return name
    return ""


def value_of(cat, rtype):
    if cat in ("交易學習筆記", "專題教學", "頻道影片") or rtype == "每週總結":
        return "core"
    if cat in ("市場數據",) or rtype in ("每日更新", "市場消息焦點", "財經日曆"):
        return "time-sensitive"
    if cat == "公告":
        return "reference"
    return "reference"


def main():
    overrides = json.loads((KB / "overrides.json").read_text()) if (KB / "overrides.json").exists() else {}
    out = {}
    for pid in all_post_ids():
        a = load_post(pid)["data"]["attributes"]
        cat = category_of(a)
        title = a.get("title") or ""
        body = DISCLAIMER.sub(" ", "\n".join([doc_text(a), attachment_text(pid), transcript_text(pid)]))
        rtype = report_type(title)
        topics = pick_topics(score_topics(title, body), len(body))
        if cat == "公告":
            topics = ["公告"]
        rec = {"category": cat, "report_type": rtype, "topics": topics, "tickers": [] if cat == "市場數據" else tickers(title, DISCLAIMER.sub(" ", doc_text(a))),
               "value": value_of(cat, rtype)}
        rec.update(overrides.get(pid, {}))
        out[pid] = rec
    KB.mkdir(exist_ok=True)
    (KB / "classification.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    tc = Counter(t for r in out.values() for t in r["topics"])
    print("topics:", dict(tc.most_common()))
    print("value:", dict(Counter(r["value"] for r in out.values())))
    print("report types:", dict(Counter(r["report_type"] for r in out.values() if r["report_type"])))
    print("top tickers:", Counter(t for r in out.values() for t in r["tickers"]).most_common(30))


if __name__ == "__main__":
    main()
