"""All test processes are offline: production credentials and CLI are forbidden."""
import sys


def _offline_audit(event,args):
    if event=='open' and isinstance(args[0],(str,bytes)):
        path=str(args[0]).replace('\\','/').lower()
        if path.endswith('/credentials-prod.json') or path.endswith('/state/local-api-key'):
            raise RuntimeError('Offline test blocked production credential access')
    if event in ('socket.connect','socket.getaddrinfo'):
        address=args[1] if event=='socket.connect' else args[0]
        host=address[0] if isinstance(address,tuple) else address
        if isinstance(host,str) and host not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('Offline test blocked non-loopback network access')
    if event=='subprocess.Popen':
        command=str(args[0] or args[1]).lower()
        if 'dreama.exe' in command:
            raise RuntimeError('Offline test blocked real Dreama CLI execution')


sys.addaudithook(_offline_audit)
