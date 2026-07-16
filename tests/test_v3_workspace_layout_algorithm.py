import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "static/v3/workspace-layout.js"


def run_node(expression: str):
    script = f"const L=require({json.dumps(str(LAYOUT))}); console.log(JSON.stringify({expression}));"
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def test_default_layout_is_deterministic_and_non_overlapping() -> None:
    expression = "(() => { const items=['preview','outline','tasks','timeline'].map(id=>({id,...L.DEFAULT_SIZES[id]})); const a=L.packModules(items); const b=L.packModules(items); return {a,b,overlap:L.hasOverlap(a.placements)}; })()"
    result = run_node(expression)

    assert result["a"] == result["b"]
    assert result["overlap"] is False
    assert result["a"]["placements"] == {
        "preview": {"col": 1, "row": 1, "cols": 8, "rows": 6},
        "outline": {"col": 9, "row": 1, "cols": 4, "rows": 3},
        "tasks": {"col": 9, "row": 4, "cols": 4, "rows": 3},
        "timeline": {"col": 1, "row": 7, "cols": 12, "rows": 4},
    }


def test_many_drag_orders_and_sizes_never_overlap() -> None:
    expression = """(() => {
      let seed=117; const ids=['preview','outline','tasks','timeline','files','history'];
      const rnd=()=>((seed=(seed*48271)%2147483647)/2147483647);
      for(let n=0;n<300;n++) {
        const order=[...ids].sort(()=>rnd()-.5);
        const items=order.map(id=>({id,cols:1+Math.floor(rnd()*15),rows:1+Math.floor(rnd()*12)}));
        const packed=L.packModules(items);
        if(L.hasOverlap(packed.placements)) return {ok:false,n,packed};
      }
      return {ok:true};
    })()"""
    assert run_node(expression) == {"ok": True}


def test_sizes_are_clamped_to_module_limits() -> None:
    result = run_node("({preview:L.clampSize('preview',{cols:1,rows:99}),timeline:L.clampSize('timeline',{cols:99,rows:1}),panel:L.clampSize('files',{cols:1,rows:99})})")
    assert result == {
        "preview": {"cols": 4, "rows": 10},
        "timeline": {"cols": 12, "rows": 2},
        "panel": {"cols": 3, "rows": 8},
    }
