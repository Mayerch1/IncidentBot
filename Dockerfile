FROM python:3

RUN pip3 install discord.py tinydb requests

WORKDIR /code
CMD ["python", "incidentBot.py"]
