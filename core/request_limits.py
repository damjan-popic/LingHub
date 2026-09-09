"""Cap request bytes before JSON parsing; never retain user text."""
from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app, limit=131072): self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        body=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect': return
            body.extend(message.get('body',b''))
            if len(body)>self.limit:
                return await JSONResponse({'detail':'Request body exceeds128KiB limit.'},status_code=413)(scope,receive,send)
            if not message.get('more_body'): break
        replayed=False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed=True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
