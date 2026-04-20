import oracledb
import os

class Config:

    SQLALCHEMY_DATABASE_URI = 'oracle+oracledb://FT_OWEN:Mee0MvpgXU!Lcp@172.18.202.5:1521/?service_name=jsqy'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_SECRET_KEY = os.urandom(24)

    WLT_TEST_DATA_REMOTE_PATH = '/WLT_TESTLOG/MAP_CP_PDF/'
    FT_TEST_DATA_REMOTE_PATH = '/FT_TESTLOG/'
    