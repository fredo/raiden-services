import json
import time
import jsonschema
from monitoring_service.utils import privkey_to_addr
from monitoring_service.messages.deserializer import deserialize
from monitoring_service.json_schema import ENVELOPE_SCHEMA


class Message:
    """Generic Raiden message"""
    json_schema = ENVELOPE_SCHEMA

    def __init__(self):
        self._type = None

    def serialize_data(self) -> dict:
        """get message data as a dict"""
        raise NotImplementedError

    @classmethod
    def deserialize_from_json(cls, data):
        """Deserialize JSON into a message instance"""
        raise NotImplementedError

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        assert isinstance(self._type, str)
        self._type = value

    def serialize_full(self, private_key):
        """Serialize message to a standardized format, including message envelope"""

        msg_data = self.assemble_message()
        msg_data['header']['sender'] = privkey_to_addr(private_key)
        msg_data = json.dumps(msg_data)
        return json.dumps({
            'signature': self.sign_data(private_key, msg_data),
            'data': msg_data
        })

    def sign_data(self, private_key, data: str):
        return '0x1111111111111111111111111111111'

    def assemble_message(self):
        assert isinstance(self.type, str)
        return {
            'header': {
                'type': self.type,
                'timestamp': time.time(),
                'sender': None
            },
            'body': self.serialize_data()
        }

    @staticmethod
    def deserialize(data):
        if isinstance(data, str):
            json_message = json.loads(data)
        else:
            json_message = data
        jsonschema.validate(json_message, Message.json_schema)
        json_data = json.loads(json_message['data'])
        cls = deserialize(json_data)
        cls.header = json_data['header']
        return cls
