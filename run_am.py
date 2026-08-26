
# dotenv is the python package responsible for handling env files
from dotenv import load_dotenv

# os is used to get the environment variables from the .env file
import os

# PipelineRunner is the main class used to run the pipeline
from academic_metrics.runners import PipelineRunner

# load_dotenv is used to load the environment variables from the .env file
load_dotenv()

# Get the environment variables from the .env file
ai_api_key = os.getenv("OPENAI_API_KEY")
mongodb_uri = os.getenv("MONGODB_URI")
db_name = os.getenv("DB_NAME")

# Set the date range you want to process
# Years is a list of years as strings you want to process
# Months is a list of strings representing the months you want processed for each year
# For example if you want to process data from 2009-2024 for all months out of the year, you would do:
# Note: the process runs left to right, so from beginning of list to the end of the list,
# so this will process 2024, then 2023, then 2022, etc.
# Data will be saved after each month is processed.
years = [
    "2024",
    "2023",
    "2022",
    "2021",
    "2020",
    "2019",
    "2018",
    "2017",
    "2016",
    "2015",
    "2014",
    "2013",
    "2012",
    "2011",
    "2010",
    "2009",
]
months = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]

# Loop over the years and months and run the pipeline for each month
# New objects are created for each month to avoid memory issues as well as to avoid overwriting data
for year in years:
    for month in months:

        # Create a new PipelineRunner object for each month
        # parameters:
        # ai_api_key: the OpenAI API key
        # crossref_affiliation: the affiliation to use for the Crossref API
        # data_from_month: the month to start collecting data from
        # data_to_month: the month to end collecting data on
        # data_from_year: the year to start collecting data from
        # data_to_year: the year to end collecting data on
        # mongodb_uri: the URL of the MongoDB server
        # db_name: the name of the database to use
        pipeline_runner = PipelineRunner(
            ai_api_key=ai_api_key,
            crossref_affiliation="Salisbury University",
            data_from_month=int(month),
            data_to_month=int(month),
            data_from_year=int(year),
            data_to_year=int(year),
            mongodb_uri=mongodb_uri,
            db_name=db_name,
        ) 

        # Run the pipeline for the current month
        pipeline_runner.run_pipeline()
