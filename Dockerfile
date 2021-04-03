FROM python:3

RUN pip3 install discord.py discord-py-slash-command tinydb requests

WORKDIR /code
CMD ["python", "incidentBot.py"]
