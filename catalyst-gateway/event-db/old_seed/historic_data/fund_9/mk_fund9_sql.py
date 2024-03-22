from __future__ import annotations

import argparse
import sqlite3
import sys
import json
from pathlib import Path
from time import gmtime, strftime


event_id = 9


def is_dir(dirpath: str | Path):
    """Check if the directory is a directory."""
    real_dir = Path(dirpath)
    if real_dir.exists() and real_dir.is_dir():
        return real_dir
    raise argparse.ArgumentTypeError(f"{dir} is not a directory.")


def is_file(filename: str):
    """Does the path exist and is it a file"""
    real_filename = Path(filename).relative_to(".")
    is_dir(real_filename.parent)
    if real_filename.is_dir():
        raise argparse.ArgumentTypeError(f"{filename} is not a file.")
    return real_filename

def epoch_to_time(epoch: int) -> str:
    """Convert an epoch time into a time string."""
    return strftime('%Y-%m-%d %H:%M:%S', gmtime(epoch))

def pg_esc(line: str | None) -> str | None:
    """Escape a string for postgres."""
    if line is None:
        return None
    return line.replace("'","''")


def event_table(con: sqlite3.Connection) -> str:
    """Return the start of the SQL file and the Event table definition."""

    cur = con.cursor()
    funds = cur.execute("SELECT * FROM funds where id = 9").fetchone()

    # fund_name = funds[1]
    fund_goal = funds[2]
    registration_snapshot_time = funds[3]
    voting_power_threshold = funds[4]
    # fund_start_time = funds[6]
    # fund_end_time = funds[7]
    insight_sharing_time = funds[9]
    proposal_submission_time = funds[10]
    refine_proposal_time = funds[11]
    finalize_proposal_time = funds[12]
    proposal_assessment_time = funds[13]
    assessment_qa_time = funds[14]
    snapshot_start_time = funds[15]
    voting_start_time = funds[16]
    voting_end_time = funds[17]
    tallying_end_time = funds[18]

    voteplans = cur.execute("SELECT * FROM voteplans LIMIT 1").fetchone()
    #chain_vote_start_time = voteplans[2]
    #chain_vote_end_time = voteplans[3]
    # chain_committee_end_time = voteplans[4]

    return f"""--sql
-- Data from Catalyst Fund {event_id}
-- AUTOGENERATED - DO NOT EDIT

-- Purge all Fund {event_id} data before re-inserting it.
DELETE FROM event WHERE row_id = {event_id};

-- Load the raw Block0 Binary from the file.
\\set block0path 'historic_data/fund_{event_id}/block0.bin'
\\set block0contents `base64 :block0path`

-- Create the Event record for Fund {event_id}

INSERT INTO event
(row_id, name, description,
 start_time,
 end_time,
 registration_snapshot_time,
 snapshot_start,
 voting_power_threshold,
 max_voting_power_pct,
 insight_sharing_start,
 proposal_submission_start,
 refine_proposals_start,
 finalize_proposals_start,
 proposal_assessment_start,
 assessment_qa_start,
 voting_start,
 voting_end,
 tallying_end,
 block0,
 block0_hash,
 committee_size,
 committee_threshold)
VALUES

({event_id}, 'Catalyst Fund {event_id}', '{fund_goal}',
 '2022-06-02 00:00:00', -- Start Time - Date/Time accurate.
 '2022-10-11 00:00:00', -- End Time   - Date/Time -- 7 days after tallying end time (confluence says 2022-09-02).
 '{epoch_to_time(registration_snapshot_time)}', -- Registration Snapshot Time -- Date/time Unknown (confluence schedule doesn't mention snapshot specifically).
 '{epoch_to_time(snapshot_start_time)}', -- Snapshot Start - Date/time (same as confluence schedule but it doesn't mention snapshot time specifically).
 {voting_power_threshold},            -- Voting Power Threshold -- Accurate
 100,                   -- Max Voting Power PCT - No max% threshold used in this fund.
 '{epoch_to_time(insight_sharing_time)}', -- Insight Sharing Start -- Accurate
 '{epoch_to_time(proposal_submission_time)}', -- Proposal Submission Start -- Date/time accurate.
 '{epoch_to_time(refine_proposal_time)}', -- Refine Proposals Start -- Date/time accurate.
 '{epoch_to_time(finalize_proposal_time)}', -- Finalize Proposals Start -- Date/time accurate.
 '{epoch_to_time(proposal_assessment_time)}', -- Proposal Assessment Start -- Date/time accurate.
 '{epoch_to_time(assessment_qa_time)}', -- Assessment QA Start -- Datetime (Confluence says 2022-07-15).
 '{epoch_to_time(voting_start_time)}', -- Voting Starts -- Date/time (Confluence says 2022-08-11).
 '{epoch_to_time(voting_end_time)}', -- Voting Ends -- Date/time (Confluence says 2022-08-25).
 '{epoch_to_time(tallying_end_time)}', -- Tallying Ends -- Date/time (Confluence schedule says 2022-09-02).
 decode(:'block0contents','base64'),
                        -- Block 0 Data - From File
 NULL,                  -- Block 0 Hash - TODO
 0,                     -- Committee Size - No Encrypted Votes
 0                      -- Committee Threshold - No Encrypted Votes
 );


-- Free large binary file contents
\\unset block0contents

"""


def objective_table(con: sqlite3.Connection) -> str:
    """Return the start of the SQL file and the Event table definition."""

    cur = con.cursor()
    challenges = cur.execute("SELECT * FROM challenges").fetchall()


    objectives = ""

    for challenge in challenges:
        id = challenge[1]
        challenge_type = challenge[2]
        title = pg_esc(challenge[3])
        description = pg_esc(challenge[4])
        rewards_total = challenge[5]
        proposers_rewards = challenge[6]
        # fund_id = challenge[6]
        challenge_url = challenge[8]

        challenge_highlights = challenge[9]
        if challenge_highlights == "null":
            challenge_highlights = None

        extra = json.dumps(
            {
                "url": {
                    "objective": challenge_url
                },
                "highlights": challenge_highlights,
            }
        )

        if len(objectives) > 0:
            objectives += ",\n"

        objectives += f"""
(
    {id}, -- Objective ID
    {event_id}, -- event id
    'catalyst-{challenge_type}', -- category
    '{title}', -- title
    '{description}', -- description
    'USD_ADA', -- Currency
    {rewards_total}, -- rewards total
    NULL, -- rewards_total_lovelace
    {proposers_rewards}, -- proposers rewards
    1, -- vote_options
    '{extra}' -- extra objective data
)
"""

    return f"""--sql
-- Challenges for Fund {event_id}
INSERT INTO objective
(
    id,
    event,
    category,
    title,
    description,
    rewards_currency,
    rewards_total,
    rewards_total_lovelace,
    proposers_rewards,
    vote_options,
    extra)
VALUES
{objectives}
;

"""


def proposal_note(con: sqlite3.Connection, proposal:str, table: str, column: str) -> str | None:
    """Get a note for a proposal."""
    cur = con.cursor()
    res = cur.execute(f"SELECT {column} FROM {table} WHERE proposal_id='{proposal}'").fetchone()
    if res is None:
        return None
    return pg_esc(res[0])


def proposals_table(con: sqlite3.Connection) -> str:
    """Return the proposals."""

    cur = con.cursor()
    proposals = cur.execute("SELECT * FROM proposals").fetchall()

    all_proposals = ""
    for proposal in proposals:
        if len(all_proposals) > 0:
            all_proposals += ',\n'

        id = proposal[0]
        proposal_id = proposal[1]
        #proposal_category = proposal[2]
        proposal_title = pg_esc(proposal[3])
        proposal_summary = pg_esc(proposal[4])
        proposal_public_key = proposal[5]
        proposal_funds = proposal[6]
        proposal_url = proposal[7]
        proposal_files_url = proposal[8]
        proposal_impact_score = proposal[9]
        proposer_name = pg_esc(proposal[10])
        proposer_contact = proposal[11]
        proposer_url = proposal[12]
        proposer_relevant_experience = pg_esc(proposal[13])
        #chain_proposal_id = proposal[14]
        #chain_proposal_index = proposal[15]
        #chain_vote_options = proposal[16]
        #chain_voteplan_id = proposal[17]
        challenge_id = f"(SELECT row_id FROM objective WHERE id={proposal[18]} AND event={event_id})"
        proposal_solution = proposal_note(con, proposal_id, "proposal_simple_challenge", "proposal_solution")
        proposal_brief = proposal_note(con, proposal_id, "proposal_community_choice_challenge", "proposal_brief")
        proposal_importance = proposal_note(con, proposal_id, "proposal_community_choice_challenge", "proposal_importance")
        proposal_goal = proposal_note(con, proposal_id, "proposal_community_choice_challenge", "proposal_goal")
        proposal_metrics = proposal_note(con, proposal_id, "proposal_community_choice_challenge", "proposal_metrics")

        category = f"(SELECT category FROM objective WHERE id={proposal[18]} AND event={event_id})"

        extra_data = {}
        if proposal_solution is not None:
            extra_data["solution"] = proposal_solution

        if proposal_brief is not None:
            extra_data["brief"] = proposal_brief

        if proposal_importance is not None:
            extra_data["importance"] = proposal_importance

        if proposal_goal is not None:
            extra_data["goal"] = proposal_goal

        if proposal_metrics is not None:
            extra_data["metrics"] = proposal_metrics

        extra = json.dumps(extra_data)

        bb_proposal_id = None

        all_proposals += f"""
(
    {proposal_id},  -- id
    {challenge_id}, -- objective
    '{proposal_title}',  -- title
    '{proposal_summary}',  -- summary
    {category}, -- category - VITSS Compat ONLY
    '{proposal_public_key}', -- Public Payment Key
    '{proposal_funds}', -- funds
    '{proposal_url}', -- url
    '{proposal_files_url}', -- files_url
    {proposal_impact_score}, -- impact_score
    '{extra}', -- extra
    '{proposer_name}', -- proposer name
    '{proposer_contact}', -- proposer contact
    '{proposer_url}', -- proposer URL
    '{proposer_relevant_experience}', -- relevant experience
    '{bb_proposal_id}',  -- bb_proposal_id
    '{{ "yes", "no" }}' -- bb_vote_options - Deprecated VitSS compat ONLY.
)
"""


    return f"""--sql
-- All Proposals for  FUND {event_id}
INSERT INTO proposal
(
    id,
    objective,
    title,
    summary,
    category,
    public_key,
    funds,
    url,
    files_url,
    impact_score,
    extra,
    proposer_name,
    proposer_contact,
    proposer_url,
    proposer_relevant_experience,
    bb_proposal_id,
    bb_vote_options
)
VALUES
{all_proposals}
;
"""


def main():
    parser = argparse.ArgumentParser(
        description=f"Process Fund {event_id}."
    )
    parser.add_argument(
        "filename",
        help=f"Sqlite3 Fund{event_id} file to read.",
        type=is_file,
    )

    args = parser.parse_args()

    # Open the sqlite file.
    con = sqlite3.connect(args.filename)

    stmt = event_table(con)
    stmt += objective_table(con)
    stmt += proposals_table(con)

    print(stmt)

    return 0

if __name__ == "__main__":
    sys.exit(main())
