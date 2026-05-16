from core.subtitle.ocr_source import SegmentAccumulator


def test_same_text_across_frames_merges_into_one_segment():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["你好世界"])
    acc.feed(0.5, ["你好世界"])
    acc.feed(1.0, ["你好世界"])
    segs = acc.finalize()
    assert len(segs) == 1
    assert segs[0].text == "你好世界"
    assert segs[0].start == 0.0
    assert segs[0].end == 1.0


def test_different_text_makes_separate_segments():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["第一句台词"])
    acc.feed(0.5, ["第一句台词"])
    acc.feed(2.0, ["完全不同的第二句"])
    segs = acc.finalize()
    assert [s.text for s in segs] == ["第一句台词", "完全不同的第二句"]
    assert segs[0].start == 0.0 and segs[0].end == 0.5
    assert segs[1].start == 2.0 and segs[1].end == 2.0


def test_reappearing_after_gap_closes_then_reopens():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["重复台词"])
    acc.feed(0.5, ["重复台词"])
    acc.feed(5.0, ["重复台词"])  # gap > 1.0 → 新段
    segs = acc.finalize()
    assert len(segs) == 2
    assert segs[0].end == 0.5
    assert segs[1].start == 5.0


def test_noise_blocks_filtered_out():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["15s", ">>>", "正常字幕内容"])
    segs = acc.finalize()
    assert [s.text for s in segs] == ["正常字幕内容"]


def test_ids_are_sequential():
    acc = SegmentAccumulator(similarity=0.7, gap=1.0)
    acc.feed(0.0, ["甲甲甲甲"])
    acc.feed(2.0, ["乙乙乙乙"])
    acc.feed(4.0, ["丙丙丙丙"])
    segs = acc.finalize()
    assert [s.id for s in segs] == [0, 1, 2]
