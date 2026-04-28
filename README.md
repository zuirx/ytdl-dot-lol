# ytdl-dot-lol
yt-dlp front-end in django 

! this project is having changes !

## Requirements
- Python 3.11.*

## Installation

- Install Python 3.11.*
- Clone this repository:
```git clone https://github.com/zuirx/ytdl-dot-lol && cd ytdl-dot-lol```
- Create a venv (and enable it):
```python -m venv venv```
- Install requirements.txt:
```pip install -r requirements.txt```
- In the project's directory, create the static files:
```python ./manage collectstatic```
- Create a .env file along side manage.py, configure the secret key like: 
```SECRET_KEY = "yoursecretwordshere"```
- Install celery in your machine (Windows: use WSL2)
- ```sudo apt install -y python3 python3-pip python3-venv redis-server```
- ```sudo systemctl enable redis-server```
- ```sudo systemctl start redis-server```
- Run the Celery worker in terminal: 
```celery -A ytdl worker -l info --pool=prefork --concurrency=4```
- Run the Celery Beat (for scheduled tasks) in another terminal:
```celery -A ytdl beat -l info```
- Run the Django server in another terminal: 
```python runserver-srv.py```
