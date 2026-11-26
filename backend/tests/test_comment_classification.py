import pytest

from backend.app.demand_analysis import labels, patch_labels


@pytest.mark.parametrize('body,content_type,intent,effective', [
    ('想了解这款医疗险的报价', '共鸣', '问价格', True),
    ('想收藏这份投保清单', '共鸣', '要资料', True),
    ('请发投保清单和报价', '共鸣', '问价格', True),
    ('这个保险的费率怎么算？', '追问', '问价格', True),
    ('我爸有高血压', '需求', '问方案', True),
    ('车险三者买两百万够不够', '追问', '问方案', True),
    ('我家已经买过了，感谢分享', '需求', '无关', False),
    ('讲得很清楚，收藏了', '共鸣', '无关', False),
    ('我爸说这价格是智商税', '需求', '无关', False),
    ('轻巧，你说的报价是多少', '反对', '无关', False),
    ('广告里的投保清单能发给我吗', '追问', '要资料', True),
    ('怎么加微信咨询', '追问', '问方案', True),
    ('广告联系VX123', '待核实', '无关', False),
    ('4800', '待核实', '待核实', False),
    ('我 30 岁', '共鸣', '无关', False),
    ('不用资料，感谢分享', '共鸣', '待核实', False),
])
def test_baokuan_content_and_intent_are_classified_independently(body, content_type, intent, effective):
    result = labels({'body': body})
    assert (result['content_type'], result['intent']) == (content_type, intent)
    assert result.get('effective_demand') is effective
    assert result['analysis_basis'] and result['intent_basis']


def test_effective_demand_respects_legacy_manual_intent_and_manual_opposition():
    imported = {'body': '只是路过', 'intent': '问价格'}
    restored = labels(imported)
    assert restored['manual_labels'] == ['intent']
    assert restored.get('effective_demand') is True
    edited = patch_labels(imported, {'content_type': '反对'})
    assert edited['intent'] == '问价格'
    assert edited['content_type'] == '反对'
    assert edited.get('effective_demand') is False


def test_manual_intent_without_original_text_is_not_effective_demand():
    result = patch_labels({'body': '  '}, {'intent': '问价格'})
    assert result['intent'] == '问价格'
    assert result['effective_demand'] is False
