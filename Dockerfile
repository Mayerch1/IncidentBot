FROM python:3

RUN pip3 install discord.py==1.5.1 tinydb requests

WORKDIR /code
CMD ["python", "incidentBot.py"]
