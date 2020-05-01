# salesinvites
Send Invites to Sales

## Run as Python ##

Install python3

```
pip3 install -r requirements.txt
python3 server.py
```

Open a browser to http://localhost:5000


## Run in Docker ##

```
docker build -t salesinvites:latest .
docker run --rm -it -p 5000:5000 salesinvites:latest
```

Open a browser to http://localhost:5000

