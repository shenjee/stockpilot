from packages.chantheory.schema import Stroke
from packages.chantheory.segments import derive_segments

def test_chan_111_issue():
    strokes = [
        Stroke(id="s15", direction="up", start_timestamp="t15", end_timestamp="t16", start_price=11.0, end_price=12.0, confirmed=True, start_fractal_id="f1", end_fractal_id="f2"),
        Stroke(id="s16", direction="down", start_timestamp="t16", end_timestamp="t17", start_price=12.0, end_price=11.5, confirmed=True, start_fractal_id="f2", end_fractal_id="f3"),
        Stroke(id="s17", direction="up", start_timestamp="t17", end_timestamp="t18", start_price=11.5, end_price=11.8, confirmed=True, start_fractal_id="f3", end_fractal_id="f4"),
        Stroke(id="s18", direction="down", start_timestamp="t18", end_timestamp="t19", start_price=11.8, end_price=10.64, confirmed=True, start_fractal_id="f4", end_fractal_id="f5"),
        Stroke(id="s19", direction="up", start_timestamp="t19", end_timestamp="t20", start_price=10.64, end_price=10.85, confirmed=True, start_fractal_id="f5", end_fractal_id="f6"),
        Stroke(id="s20", direction="down", start_timestamp="t20", end_timestamp="t21", start_price=10.85, end_price=10.62, confirmed=True, start_fractal_id="f6", end_fractal_id="f7"),
        Stroke(id="s21", direction="up", start_timestamp="t21", end_timestamp="t22", start_price=10.62, end_price=11.1, confirmed=True, start_fractal_id="f7", end_fractal_id="f8"),
        Stroke(id="s22", direction="down", start_timestamp="t22", end_timestamp="t23", start_price=11.1, end_price=10.77, confirmed=True, start_fractal_id="f8", end_fractal_id="f9"),
        Stroke(id="s23", direction="up", start_timestamp="t23", end_timestamp="t24", start_price=10.77, end_price=11.35, confirmed=True, start_fractal_id="f9", end_fractal_id="f10"),
        Stroke(id="s24", direction="down", start_timestamp="t24", end_timestamp="t25", start_price=11.35, end_price=11.0, confirmed=True, start_fractal_id="f10", end_fractal_id="f11"),
        Stroke(id="s25", direction="up", start_timestamp="t25", end_timestamp="t26", start_price=11.0, end_price=11.5, confirmed=True, start_fractal_id="f11", end_fractal_id="f12"),
    ]
    
    segments = derive_segments(strokes)
    for seg in segments:
        print(f"Segment {seg.direction} from {seg.start_timestamp} ({seg.start_price}) to {seg.end_timestamp} ({seg.end_price})")
        print(f"  Strokes: {seg.stroke_ids}")
        print(f"  Meta feature_break: {seg.meta.get('feature_sequence_break')}")

if __name__ == "__main__":
    test_chan_111_issue()
