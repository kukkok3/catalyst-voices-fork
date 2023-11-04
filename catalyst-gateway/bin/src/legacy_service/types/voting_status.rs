use serde::{ser::Serializer, Serialize};

use super::SerdeType;
use crate::event_db::types::{objective::ObjectiveId, voting_status::VotingStatus};

impl Serialize for SerdeType<VotingStatus> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where S: Serializer {
        #[derive(Serialize)]
        struct VotingStatusSerde<'a> {
            objective_id: SerdeType<&'a ObjectiveId>,
            open: bool,
            #[serde(skip_serializing_if = "Option::is_none")]
            settings: Option<&'a String>,
        }
        VotingStatusSerde {
            objective_id: SerdeType(&self.objective_id),
            open: self.open,
            settings: self.settings.as_ref(),
        }
        .serialize(serializer)
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::event_db::types::objective::ObjectiveId;

    #[test]
    fn voting_status_json_test() {
        let voting_status = SerdeType(VotingStatus {
            objective_id: ObjectiveId(1),
            open: false,
            settings: None,
        });

        let json = serde_json::to_value(voting_status).unwrap();
        assert_eq!(
            json,
            json!(
                {
                    "objective_id": 1,
                    "open": false,
                }
            )
        );

        let voting_status = SerdeType(VotingStatus {
            objective_id: ObjectiveId(1),
            open: true,
            settings: Some("some settings".to_string()),
        });

        let json = serde_json::to_value(voting_status).unwrap();
        assert_eq!(
            json,
            json!(
                {
                    "objective_id": 1,
                    "open": true,
                    "settings": "some settings",
                }
            )
        );
    }
}