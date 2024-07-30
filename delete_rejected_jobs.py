import pymysql
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Database configuration from environment variables
DB_HOST = os.getenv('DB_HOST')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASS')
DB_NAME = os.getenv('DB_NAME')

def delete_rejected_entries():
    try:
        # Connect to the database
        connection = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        
        with connection.cursor() as cursor:
            # SQL query to delete entries with status 'Rejected'
            delete_query = "DELETE FROM jobs WHERE status = 'Rejected'"
            
            # Execute the query
            # cursor.execute(delete_query)
            
            # Commit the changes
            connection.commit()
            
            # print("Entries with status 'Rejected' have been deleted.")
    
    except pymysql.MySQLError as e:
        print(f"Error: {e}")
    
    finally:
        # Close the connection
        if connection:
            connection.close()

if __name__ == "__main__":
    delete_rejected_entries()
