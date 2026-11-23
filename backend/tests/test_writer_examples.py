from backend.tests.test_writer_profiles import env


def test_industry_examples_preserve_inputs_and_do_not_save_or_call_model(env):
    _, c, ip, users = env
    r = users['member'].post('/api/writers/examples', json={'industry': '家装设计', 'ip_id': ip['id'], 'skill': 'original-video-writer', 'answers': ['我的真实身份', '', '', '已有真实证据']})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data['answers']) == 12
    assert data['answers'][0] == '我的真实身份' and data['answers'][3] == '已有真实证据'
    assert data['answers'][10].isdigit()
    assert '家装' in ''.join(data['answers'])
    assert '待核实' in ''.join(data['answers'])
    assert data['generated_indices'] == [1, 2, 4, 5, 6, 7, 8, 9, 10, 11]
    assert c.get('/api/writers').json() == []
    assert c.get('/api/jobs').json() == []
    w = c.post('/api/writers', json={'industry': '家装设计', 'ip_id': ip['id'], 'name': '装修写手', 'answers': data['answers']}).json()
    assert w['industry'] == '家装设计'
    updated = c.patch('/api/writers/'+w['id'], json={'version': w['version'], 'industry': '室内设计'})
    assert updated.json()['industry'] == '室内设计'


def test_example_validation_and_ip_access(env):
    _, c, ip, users = env
    assert c.post('/api/writers/examples', json={'industry': '  '}).status_code == 422
    assert users['member'].post('/api/writers/examples', json={'industry': '保险', 'ip_id': 'outside'}).status_code == 403
    assert c.post('/api/writers/examples', json={'industry': '保险', 'answers': ['']*10+['不正确']}).status_code == 422
    x = c.post('/api/writers/examples', json={'industry': '冷门专业服务', 'skill': 'gzh-writer'}).json()
    assert len(x['answers']) == 12 and '冷门专业服务' in ''.join(x['answers'])
    assert '例' in x['notice']
