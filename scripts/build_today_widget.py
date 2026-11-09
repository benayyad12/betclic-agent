"""Génère un widget HTML filtrable des matchs du jour depuis le JSON de ``today --json``.

Usage :
    python scripts/build_today_widget.py --input data/matches_today.json --output data/today.html

Le widget est autonome (aucune ressource externe) : recherche par équipe/compétition et
filtres par statut (à venir / en cours / terminés / diffusion Betclic). Il reprend le
thème du chat via les variables CSS (--foreground, --muted-foreground, --accent,
--border). Le statut est estimé d'après l'heure : le backend n'expose pas de statut.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from betclic_agent.timing import IN_PLAY, PAST, UPCOMING, local_time, match_status  # noqa: E402

STATUS_LABELS = {IN_PLAY: "en cours", UPCOMING: "à venir", PAST: "terminé"}
FILTERS = (("all", "Tous"), (UPCOMING, "À venir"), (IN_PLAY, "En cours"), (PAST, "Terminés"), ("stream", "Diffusion"))

STYLE = """
.wrap { color: var(--foreground); font-size: 13px; }
.head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.head h3 { margin: 0; font-size: 15px; font-weight: 600; }
.stat { color: var(--muted-foreground); }
.controls { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
input[type=search] {
  flex: 1 1 220px; padding: 6px 9px; border: 1px solid var(--border);
  border-radius: 6px; background: transparent; color: var(--foreground); font: inherit;
}
button {
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px;
  background: transparent; color: var(--foreground); font: inherit; cursor: pointer;
}
button.on { background: var(--accent); border-color: var(--accent); }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--border); }
th { color: var(--muted-foreground); font-weight: 500; font-size: 12px; }
td.time { white-space: nowrap; color: var(--muted-foreground); font-variant-numeric: tabular-nums; }
td.comp, td.status { color: var(--muted-foreground); white-space: nowrap; }
tr.playing td.time, tr.playing td.status { color: #e5484d; font-weight: 600; }
tr.past td.match { color: var(--muted-foreground); }
.empty { color: var(--muted-foreground); padding: 12px 0; }
.note { color: var(--muted-foreground); margin-top: 8px; font-size: 12px; }
"""

SCRIPT = """
const rows = [...document.querySelectorAll('tbody tr')];
const search = document.getElementById('q');
const buttons = [...document.querySelectorAll('button[data-filter]')];
let filter = 'all';

function apply() {
  const q = search.value.trim().toLowerCase();
  let shown = 0;
  for (const row of rows) {
    const okSearch = row.dataset.search.includes(q);
    const okFilter = filter === 'all'
      || (filter === 'stream' ? row.dataset.stream === '1' : row.dataset.status === filter);
    const show = okSearch && okFilter;
    row.style.display = show ? '' : 'none';
    if (show) shown++;
  }
  document.getElementById('count').textContent = shown;
  document.getElementById('empty').style.display = shown ? 'none' : '';
}

search.addEventListener('input', apply);
for (const button of buttons) {
  button.addEventListener('click', () => {
    filter = button.dataset.filter;
    buttons.forEach(b => b.classList.toggle('on', b === button));
    apply();
  });
}
apply();
"""


def build_rows(matches: list[dict], now: datetime) -> str:
    lines = []
    for m in sorted(matches, key=lambda m: (m["date"] or "", m["competition"] or "")):
        status = match_status(m["date"], now)
        stream = bool(m["live_stream"])
        when = local_time(m["date"])
        label = STATUS_LABELS.get(status, "—")
        if stream:
            label += " 📺"
        search = f"{m['name']} {m['competition']} {when} {label}".lower()
        lines.append(
            "<tr class=\"{cls}\" data-status=\"{status}\" data-stream=\"{stream}\" data-search=\"{search}\">"
            "<td class=\"time\">{when}</td>"
            "<td class=\"comp\">{comp}</td>"
            "<td class=\"match\">{name}</td>"
            "<td class=\"status\">{label}</td>"
            "<td class=\"comp\">{mid}</td>"
            "</tr>".format(
                cls="playing" if status == IN_PLAY else ("past" if status == PAST else ""),
                status=html.escape(status, quote=True),
                stream=1 if stream else 0,
                search=html.escape(search, quote=True),
                when=html.escape(when),
                comp=html.escape(m["competition"] or "—"),
                name=html.escape(m["name"] or "—"),
                label=html.escape(label),
                mid=m["id"],
            )
        )
    return "\n".join(lines)


def build_html(data: dict, now: datetime) -> str:
    matches = data["matches"]
    counts = {status: 0 for status in (UPCOMING, IN_PLAY, PAST)}
    for m in matches:
        status = match_status(m["date"], now)
        if status in counts:
            counts[status] += 1
    streamed = sum(1 for m in matches if m["live_stream"])
    filters = "".join(
        '<button data-filter="{value}"{on}>{label}</button>'.format(
            value=value, label=html.escape(label), on=' class="on"' if value == "all" else ""
        )
        for value, label in FILTERS
    )
    return f"""<div class="wrap">
  <div class="head">
    <h3>Matchs football — {html.escape(data['day'])}</h3>
    <span class="stat">marché {html.escape(data.get('market', 'CI'))} ·
      <span id="count">{len(matches)}</span> affiché(s) sur {len(matches)} ·
      {counts[UPCOMING]} à venir · {counts[IN_PLAY]} en cours · {counts[PAST]} terminés ·
      {streamed} 📺 diffusion Betclic</span>
  </div>
  <div class="controls">
    <input id="q" type="search" placeholder="Filtrer par équipe ou compétition…">
    {filters}
  </div>
  <table>
    <thead><tr><th>Heure</th><th>Compétition</th><th>Match</th><th>Statut</th><th>id</th></tr></thead>
    <tbody>
{build_rows(matches, now)}
    </tbody>
  </table>
  <div id="empty" class="empty">Aucun match ne correspond au filtre.</div>
  <div class="note">Heures en UTC (= heure locale ivoirienne). Statut estimé d'après l'heure de
    coup d'envoi : le backend Betclic ne fournit pas de statut fiable.</div>
</div>
<style>{STYLE}</style>
<script>{SCRIPT}</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Widget HTML des matchs du jour.")
    parser.add_argument("--input", default="data/matches_today.json")
    parser.add_argument("--output", default="data/today.html")
    parser.add_argument("--now", default=None, help="Heure de référence ISO (tests)")
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(build_html(data, now), encoding="utf-8")
    print(f"Widget écrit : {args.output} ({len(data['matches'])} matchs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
