from core.transcribe.prompt import build_prompt


def test_prompt_has_core_constraints():
    p = build_prompt(image_count=7)
    assert "7" in p                 # 告知张数
    assert "原文" in p              # 保留原文
    assert "风景" in p              # 风景图标注约定
    assert "### 图" in p            # 分隔约定
