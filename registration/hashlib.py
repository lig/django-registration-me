import six

if six.PY3:
    from hashlib import sha1
else:
    from sha import new as sha1
