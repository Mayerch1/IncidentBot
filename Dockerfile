FROM python:3

RUN pip3 install discord.py tinydb

WORKDIR /code
CMD ["python", "incidentBot.py"]
