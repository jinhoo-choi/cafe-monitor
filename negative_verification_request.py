"""검수 브랜치의 제한된 메타데이터 요청만 처리한다. 운영 main에서는 실행 금지."""
import json
import os
from pathlib import Path

from negative_queue import JOB_ID, Queue, connection


def arguments(request):
    if os.environ.get('GITHUB_REF') != 'refs/heads/codex/negative-subscription-verification':
        raise ValueError('검수 브랜치 외 실행 금지')
    if not isinstance(request, dict) or not set(request) <= {'operation', 'job_id', 'fixture_id', 'request_id', 'self_test'}:
        raise ValueError('공개 요청에는 제한된 메타데이터만 허용')
    operation = request.get('operation')
    if operation not in {'connection', 'collect', 'consume'}:
        raise ValueError('지원하지 않는 검수 요청')
    if operation == 'consume' and request.get('self_test') is not True:
        raise ValueError('본인 검수만 허용')
    for field in ('job_id', 'fixture_id', 'request_id'):
        if field in request and (not isinstance(request[field], str) or not JOB_ID.fullmatch(request[field])):
            raise ValueError('검수 식별자 오류')
    if operation == 'consume' and not request.get('job_id'):
        raise ValueError('검수 회차 미지정')
    return request


def main():
    request = arguments(json.loads(Path('negative_verification_request.json').read_text()))
    queue = Queue()
    if request['operation'] == 'connection':
        connection(queue)
    else:
        from negative_verification import collect, consume
        if request['operation'] == 'collect':
            collect(queue, request.get('fixture_id'))
        else:
            consume(queue, request['job_id'])


if __name__ == '__main__':
    main()
