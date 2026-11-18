from src.ollama_policy import bounded_request,server_environment,compact_local_prompt


def test_memory_policy_overrides_unbounded_caller_settings_without_mutating_input():
    original={'keep_alive':-1,'options':{'num_ctx':32768,'temperature':.3}}
    actual=bounded_request(original)
    assert actual['keep_alive']=='60s' and actual['options']['num_ctx']==8192
    assert original['keep_alive']==-1 and original['options']['num_ctx']==32768
    assert server_environment({'OLLAMA_NUM_PARALLEL':'4','EXISTING':'preserved'})['OLLAMA_NUM_PARALLEL']=='1'


def test_local_prompt_retains_role_and_assignment_and_discloses_abbreviation():
    prompt='ROLE: analyst\n'+('evidence '*10000)+'\nASSIGNMENT: compare creators'
    result=compact_local_prompt(prompt)
    assert len(result)<=18000
    assert result.startswith('ROLE: analyst') and result.endswith('ASSIGNMENT: compare creators')
    assert 'evidence abbreviated' in result
    assert compact_local_prompt('short')=='short'
