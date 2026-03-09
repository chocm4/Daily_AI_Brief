from typing import Any, Dict, List


def _fmt_pct(v):
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "데이터 없음"


def _top_moves(market: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    rows = []
    for x in market or []:
        try:
            chg = float(x.get('ret1d_pct')) if x.get('ret1d_pct') is not None else None
        except Exception:
            chg = None
        if chg is None:
            continue
        rows.append({
            'name': x.get('name') or x.get('asset') or '',
            'kind': x.get('kind', 'price'),
            'ret1d_pct': chg,
            'level': x.get('level'),
        })
    rows.sort(key=lambda z: abs(z['ret1d_pct']), reverse=True)
    return rows[:top_k]


def build_market_context(
    market: List[Dict[str, Any]] | None,
    sectors_kr: List[Dict[str, Any]] | None = None,
    krx_flows: Dict[str, Any] | None = None,
    krx_flow_tops: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    market = market or []
    sectors_kr = sectors_kr or []
    krx_flows = krx_flows or {}
    krx_flow_tops = krx_flow_tops or {}

    context: Dict[str, Any] = {
        'market_top_moves': _top_moves(market, top_k=6),
        'sector_summary': {'leaders': [], 'laggards': [], 'dispersion_pctp': None},
        'flow_summary': {},
        'style_flags': [],
    }

    if sectors_kr:
        ordered = sorted([x for x in sectors_kr if x.get('ret1d_pct') is not None], key=lambda z: float(z['ret1d_pct']), reverse=True)
        leaders = ordered[:3]
        laggards = list(reversed(ordered[-3:])) if len(ordered) >= 3 else ordered[-3:]
        dispersion = None
        if ordered:
            dispersion = round(float(ordered[0]['ret1d_pct']) - float(ordered[-1]['ret1d_pct']), 2)
        context['sector_summary'] = {
            'leaders': [{'name': x.get('name'), 'ret1d_pct': float(x.get('ret1d_pct'))} for x in leaders],
            'laggards': [{'name': x.get('name'), 'ret1d_pct': float(x.get('ret1d_pct'))} for x in laggards],
            'dispersion_pctp': dispersion,
        }
        if dispersion is not None and dispersion >= 2.5:
            context['style_flags'].append('업종 차별화가 큰 장세')

    for market_name, panel in krx_flows.items():
        net = (panel or {}).get('net_buy_1e8krw') or {}
        if not net:
            continue
        foreign = net.get('외국인')
        inst = net.get('기관합계')
        retail = net.get('개인')
        summary = {
            'foreign_1e8krw': foreign,
            'institution_1e8krw': inst,
            'retail_1e8krw': retail,
            'top_foreign': ((krx_flow_tops.get(market_name) or {}).get('외국인') or [])[:5],
            'top_retail': ((krx_flow_tops.get(market_name) or {}).get('개인') or [])[:5],
        }
        context['flow_summary'][market_name] = summary

        vals = [('외국인', abs(foreign or 0)), ('기관', abs(inst or 0)), ('개인', abs(retail or 0))]
        leader = sorted(vals, key=lambda x: x[1], reverse=True)[0][0]
        context['style_flags'].append(f'{market_name} 수급 주도: {leader}')

    return context
