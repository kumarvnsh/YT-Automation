"""Resource limits for the always-on 16 GB automation laptop."""

SERVER_SETTINGS={
    'OLLAMA_KEEP_ALIVE':'60s',
    'OLLAMA_MAX_LOADED_MODELS':'1',
    'OLLAMA_NUM_PARALLEL':'1',
    'OLLAMA_CONTEXT_LENGTH':'4096',
    'OLLAMA_MAX_QUEUE':'4',
}


def server_environment(environment):
    return {**environment,**SERVER_SETTINGS}


def bounded_request(payload):
    result={**payload,'keep_alive':'60s'}
    options=dict(payload.get('options') or {})
    options['num_ctx']=max(1024,min(8192,int(options.get('num_ctx',4096))))
    result['options']=options
    return result


def compact_local_prompt(text,limit=18000):
    if len(text)<=limit:return text
    # Keep the role/constraints and the final assignment. Explicitly disclose
    # that the middle evidence was abbreviated rather than imply a full review.
    return text[:3000]+'\n[Local evidence abbreviated to fit memory limits; do not infer missing numbers.]\n'+text[-(limit-3100):]
