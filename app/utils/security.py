from flask import request

def get_client_ip()->str:
    return (request.remote_addr or 'unknown')[:45]
