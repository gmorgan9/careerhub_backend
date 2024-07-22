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

def get_message_html(msg):
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            payload = part.get_payload(decode=True)

            # Default to UTF-8 based on Content-Type
            encoding = 'utf-8'
            try:
                # Attempt to decode using UTF-8
                html_body = payload.decode(encoding)
            except (UnicodeDecodeError, TypeError) as e:
                # Handle errors gracefully and print a sample for debugging
                print(f"Encoding error with UTF-8: {e}")
                print(f"Payload sample (first 1000 bytes): {payload[:1000]}")
                # Fallback to using chardet for encoding detection
                detected_encoding = chardet.detect(payload).get('encoding', 'utf-8')
                print(f"Detected encoding: {detected_encoding}")
                try:
                    html_body = payload.decode(detected_encoding, errors='replace')
                except (UnicodeDecodeError, TypeError) as fallback_error:
                    print(f"Fallback encoding error: {fallback_error}")
                    html_body = payload.decode('utf-8', errors='replace')
                    print("Fallback to 'utf-8' with replacement characters for decoding.")

            return {
                'subject': msg['subject'],
                'from': msg['from'],
                'html_body': html_body,
            }

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

def extract_job_details_from_indeed(html_body):
    soup = BeautifulSoup(html_body, 'html.parser')

    job_title = None
    company = None
    location = None
    is_remote = False
    job_link = None

    # Extract job title and link
    job_title_elem = soup.find('a', style=re.compile(r'color:#2d2d2d;text-decoration: underline;'), href=True)
    if job_title_elem:
        job_title = job_title_elem.get_text(strip=True)
        job_link = job_title_elem['href']

    # Extract company and location
    company_location_elem = soup.find('p', style=re.compile(r'font-family:\'Noto Sans\', Helvetica, Arial, sans-serif;font-size:16px;line-height:24px;font-weight:normal;color:#2D2D2D;Margin:0;padding:0;'))
    if company_location_elem:
        company_location_text = company_location_elem.get_text(strip=True)
        # Split on the '-' to separate company and location
        parts = company_location_text.split(' - ', 1)
        if len(parts) == 2:
            company = parts[0].strip()
            location = parts[1].strip()
            # Remove any zip code from the location
            location = re.sub(r'\d{5}(-\d{4})?$', '', location).strip()
            if location.lower() == 'remote':
                is_remote = True
                location = 'Remote'

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

def insert_job_details(job_details):
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
            idno = generate_unique_id(cursor)
            status = 'Applied'
            if job_details['is_remote']:
                location = 'Remote'
            else:
                location = job_details['location']
                if location is not None:
                    location = location.replace("(On-site)", "").strip()
                else:
                    location = 'Unknown'
            sql = """
                INSERT INTO jobs (idno, job_title, company, location, job_link, status)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (
                idno,
                job_details['job_title'],
                job_details['company'],
                location,
                job_details['job_link'],
                status
            ))
            connection.commit()
            inserted = True
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
            # print('')
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
                details = get_message_html(msg)
                if details:
                    emails_checked += 1
                    subject = details['subject']
                    from_email = details['from']
                    html_body = details['html_body']
                    if "your application was sent" in subject.lower() and "linkedin" in from_email.lower():
                        job_details = extract_job_details_from_html(html_body)
                        if insert_job_details(job_details):
                            emails_inserted += 1
                            inserted_jobs.append(job_details)
                    elif "indeed application" in subject.lower() and "indeed" in from_email.lower():
                        job_details = extract_job_details_from_indeed(html_body)
                        if insert_job_details(job_details):
                            emails_inserted += 1
                            inserted_jobs.append(job_details)

                # Mark the email as read
                # mail.store(num, '+FLAGS', '\\Seen')
                # # Mark the email for deletion
                # mail.store(num, '+FLAGS', '\\Deleted')

            except Exception as e:
                print(f"Error processing email {num}: {e}")

        # Permanently delete emails marked for deletion
        mail.expunge()
        mail.logout()

        send_summary_to_slack(emails_checked, emails_inserted, inserted_jobs)

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()
