import os
import imaplib
import email
import chardet
import re
from bs4 import BeautifulSoup
import pymysql
import random
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

def get_message_html(msg, message_id):
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            payload = part.get_payload(decode=True)
            detected_encoding = chardet.detect(payload)['encoding']
            
            # Default to utf-8 if detected encoding is None or if there's an encoding issue
            if detected_encoding is None:
                detected_encoding = 'utf-8'
            
            try:
                html_body = payload.decode(detected_encoding)
            except (UnicodeDecodeError, TypeError) as e:
                print(f"Encoding error with detected encoding '{detected_encoding}': {e}")
                # Fallback to 'utf-8' with replacement for unrecognized characters
                html_body = payload.decode('utf-8', errors='replace')
            
            return {
                'subject': msg['subject'],
                'from': msg['from'],
                'html_body': html_body,
                'message_id': message_id,
            }
        
def log_problematic_email(payload, detected_encoding):
    # Save the payload to a file for manual inspection
    with open('problematic_email.html', 'wb') as file:
        file.write(payload)
    print(f"Problematic email saved for inspection. Encoding: {detected_encoding}")

# Example usage inside get_message_html
try:
    html_body = payload.decode(detected_encoding)
except (UnicodeDecodeError, TypeError) as e:
    print(f"Encoding error with detected encoding '{detected_encoding}': {e}")
    log_problematic_email(payload, detected_encoding)
    html_body = payload.decode('utf-8', errors='replace')


def extract_job_details_from_html(html_body):
    soup = BeautifulSoup(html_body, 'html.parser')

    job_title = None
    company = None
    location = None
    is_remote = False
    job_link = None

    job_title_elem = soup.find('a', class_='text-md leading-regular text-color-brand', href=True)
    if job_title_elem:
        job_title = job_title_elem.get_text(strip=True)
        job_link = job_title_elem['href']

    company_location_elem = soup.find('p', class_='text-system-gray-100 text-sm leading-[20px]')
    if company_location_elem:
        company_location_text = company_location_elem.get_text(strip=True)
        match = re.match(r'^(.*?)\s*(?:&middot;|\u00B7|\u2022)\s*(.*?)\s*\((Remote)\)?\s*$', company_location_text)
        if match:
            company = match.group(1).strip()
            location = match.group(2).strip()
            is_remote = True
        else:
            match = re.match(r'^(.*?)\s*(?:&middot;|\u00B7|\u2022)\s*(.*?)\s*$', company_location_text)
            if match:
                company = match.group(1).strip()
                location = match.group(2).strip()
                is_remote = False

    return {
        'job_title': job_title,
        'company': company,
        'location': location,
        'is_remote': is_remote,
        'job_link': job_link,
    }

def generate_unique_id(cursor):
    while True:
        idno = ''.join([str(random.randint(0, 9)) for _ in range(7)])
        cursor.execute("SELECT idno FROM jobs WHERE idno = %s", (idno,))
        if not cursor.fetchone():
            return idno

def job_exists(cursor, message_id):
    cursor.execute("SELECT email_message_id FROM jobs WHERE email_message_id = %s", (message_id,))
    return cursor.fetchone() is not None

def insert_job_details(job_details, message_id):
    connection = pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        db=os.getenv('DB_NAME'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    inserted = False

    try:
        with connection.cursor() as cursor:
            if not job_exists(cursor, message_id):
                idno = generate_unique_id(cursor)
                status = 'Applied'
                if job_details['is_remote']:
                    location = job_details['location'] + ' (Remote)'
                else:
                    location = job_details['location']
                sql = """
                    INSERT INTO jobs (idno, job_title, company, location, job_link, email_message_id, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                cursor.execute(sql, (
                    idno,
                    job_details['job_title'],
                    job_details['company'],
                    location,
                    job_details['job_link'],
                    message_id,
                    status
                ))
                connection.commit()
                inserted = True
            # Removed the else block containing the print statement
    finally:
        connection.close()
    
    return inserted


def send_summary_to_slack(emails_checked, emails_inserted, inserted_jobs):
    message = f"Emails Scanned: {emails_checked}\nEmails inserted into the database: {emails_inserted}\n"
    job_insert = ""
    if emails_inserted > 0:
        job_insert = "Jobs Inserted:\n"
        for index, job in enumerate(inserted_jobs, start=1):
            job_insert += f"({index}) {job['job_title']} - {job['company']}\n"
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "CareerHub Job Application Script",
                "emoji": True
            }
        },
        {
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": message,
                "emoji": True
            }
        },
        {
            "type": "divider"
        }
    ]
    if job_insert:
        blocks.append({
            "type": "section",
            "text": {
                "type": "plain_text",
                "text": job_insert,
                "emoji": True
            }
        })
        blocks.append({
            "type": "divider"
        })
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "View All Jobs",
                    "emoji": True
                },
                "url": "https://careerhub.morganserver.com/console/job/all-jobs/",
                "action_id": "view_all_jobs_button"
            }
        ]
    })
    payload = {
        'blocks': blocks
    }
    response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    if response.status_code != 200:
        print(f"Failed to send Slack summary: {response.text}")

def main():
    load_dotenv()
    email_user = os.getenv('EMAIL_USER')
    email_pass = os.getenv('EMAIL_PASS')

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com')
        mail.login(email_user, email_pass)

        # Select the "Job Applications" folder
        mail.select('"Job Applications"')

        # Search for all emails in the selected folder
        status, data = mail.search(None, 'ALL')
        if status != 'OK':
            print(f"Error searching emails: {status}")
            return

        mail_ids = data[0].split()
        if not mail_ids:
            print('No new messages in "Job Applications".')
            return

        emails_checked = 0
        emails_inserted = 0
        inserted_jobs = []

        for num in mail_ids:
            try:
                status, data = mail.fetch(num, '(BODY.PEEK[])')
                if status != 'OK':
                    print(f"Error fetching email {num}: {status}")
                    continue

                raw_email = data[0][1] if data[0] else None
                if raw_email is None:
                    print(f"No data returned for email {num}")
                    continue

                msg = email.message_from_bytes(raw_email)
                details = get_message_html(msg, num.decode())
                if details:
                    emails_checked += 1
                    subject = details['subject']
                    from_email = details['from']
                    html_body = details['html_body']
                    message_id = details['message_id']
                    if "your application was sent" in subject.lower() and "linkedin" in from_email.lower():
                        job_details = extract_job_details_from_html(html_body)
                        if insert_job_details(job_details, message_id):
                            emails_inserted += 1
                            inserted_jobs.append(job_details)
            except Exception as e:
                print(f"Error processing email {num}: {e}")

        mail.logout()
        send_summary_to_slack(emails_checked, emails_inserted, inserted_jobs)

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()