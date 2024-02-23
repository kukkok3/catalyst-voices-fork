use std::{error::Error, str::FromStr, sync::Arc};

use async_recursion::async_recursion;
use cardano_chain_follower::{
    network_genesis_values, ChainUpdate, Follower, FollowerConfig, FollowerConfigBuilder, Network,
    Point,
};
use tokio::{task::JoinHandle, time};
use tracing::{error, info};

use crate::event_db::{
    legacy::queries::event::{
        config::{ConfigQueries, FollowerMeta, NetworkMeta},
        follower::{BlockHash, FollowerQueries, LastUpdate, SlotNumber},
    },
    EventDB,
};

const DATA_NOT_STALE: i64 = 1;

#[async_recursion]
/// Start followers as per defined in the config
pub(crate) async fn start_followers(
    configs: (Vec<NetworkMeta>, FollowerMeta), db: Arc<EventDB>, data_refresh_tick: u64,
    check_config_tick: u64, machine_id: String,
) -> Result<(), Box<dyn Error>> {
    // spawn followers and obtain thread handlers for control and future cancellation
    let follower_tasks = spawn_followers(
        configs.clone(),
        db.clone(),
        data_refresh_tick,
        machine_id.clone(),
    )
    .await?;

    // Followers should continue indexing until config has changed
    let mut interval = time::interval(time::Duration::from_secs(check_config_tick));
    let config = loop {
        interval.tick().await;
        match db.get_config().await.map(|config| config) {
            Ok(config) => {
                if configs != config {
                    info!("Config has changed! restarting");
                    break Some(config);
                }
            },
            Err(err) => {
                error!("No config {:?}", err);
                break None;
            },
        }
    };

    // Config has changed, terminate all followers and restart with new config.
    info!("Terminating followers");
    for task in follower_tasks {
        task.abort()
    }

    match config {
        Some(config) => {
            info!("Restarting followers with new config");
            start_followers(
                config,
                db,
                data_refresh_tick,
                check_config_tick,
                machine_id.clone(),
            )
            .await?;
        },
        None => return Err("Config has been deleted...".into()),
    }

    Ok(())
}

/// Spawn follower threads and return handlers
async fn spawn_followers(
    configs: (Vec<NetworkMeta>, FollowerMeta), db: Arc<EventDB>, data_refresh_tick: u64,
    machine_id: String,
) -> Result<Vec<JoinHandle<()>>, Box<dyn Error>> {
    let mut follower_tasks = Vec::new();
    for config in &configs.0 {
        info!("starting follower for {:?}", config.network);

        let network = Network::from_str(&config.network)?;

        // Tick until data is stale then start followers
        let mut interval = time::interval(time::Duration::from_secs(data_refresh_tick));
        let task_handler = loop {
            interval.tick().await;

            // Check if previous follower has indexed, if so, return last update point in order to
            // continue indexing from that point If there was no previous follower, we
            // start from genesis point.
            let (slot_no, block_hash, last_updated) =
                find_last_update_point(db.clone(), &config.network).await?;

            // Data is marked as stale after N seconds with no updates.
            let threshold = match last_updated {
                Some(last_update) => last_update.timestamp(),
                None => {
                    info!("No previous followers, staleness not relevant. Start follower from genesis.");
                    DATA_NOT_STALE
                },
            };

            // Threshold which defines if data is stale and ready to update or not
            if chrono::offset::Utc::now().timestamp() - threshold > configs.1.timing_pattern.into()
            {
                info!(
                    "Last update is stale for network {} - ready to update, starting follower now.",
                    config.network
                );
                let task_handler = init_follower(
                    network,
                    config.relay.clone(),
                    (slot_no, block_hash),
                    db.clone(),
                    machine_id.clone(),
                )
                .await?;
                break task_handler;
            } else {
                info!(
                    "Data is still fresh for network {}, tick until data is stale",
                    config.network
                );
            }
        };

        follower_tasks.push(task_handler);
    }

    Ok(follower_tasks)
}

/// Establish point at which the last follower stopped updating in order to pick up where
/// it left off. If there was no previous follower, start indexing from genesis point.
async fn find_last_update_point(
    db: Arc<EventDB>, network: &String,
) -> Result<(Option<SlotNumber>, Option<BlockHash>, Option<LastUpdate>), Box<dyn Error>> {
    let (slot_no, block_hash, last_updated) =
        match db.last_updated_metadata(network.to_string()).await {
            Ok((slot_no, block_hash, last_updated)) => {
                info!(
                "Previous follower stopped updating at Slot_no: {} block_hash:{} last_updated: {}",
                slot_no, block_hash, last_updated
            );
                (Some(slot_no), Some(block_hash), Some(last_updated))
            },
            Err(err) => {
                info!("No previous followers, start from genesis. Db msg: {}", err);
                (None, None, None)
            },
        };

    Ok((slot_no, block_hash, last_updated))
}

/// Initiate single follower and return task handler for future control over spawned
/// threads
async fn init_follower(
    network: Network, relay: String, start_from: (Option<SlotNumber>, Option<BlockHash>),
    db: Arc<EventDB>, machine_id: String,
) -> Result<tokio::task::JoinHandle<()>, Box<dyn Error>> {
    let follower_cfg = generate_follower_config(start_from).await?;

    let mut follower = Follower::connect(&relay, network, follower_cfg).await?;

    let genesis_values =
        network_genesis_values(&network).ok_or("Obtaining genesis values should be infallible")?;

    let task = tokio::spawn(async move {
        loop {
            let chain_update = match follower.next().await {
                Ok(chain_update) => chain_update,
                Err(err) => {
                    error!(
                        "Unable receive next update from follower {:?} - skip..",
                        err
                    );
                    continue;
                },
            };

            match chain_update {
                ChainUpdate::Block(data) => {
                    let block = match data.decode() {
                        Ok(block) => block,
                        Err(err) => {
                            error!("Unable to decode block {:?} - skip..", err);
                            continue;
                        },
                    };

                    // Parse block

                    let epoch = match block.epoch(&genesis_values).0.try_into() {
                        Ok(epoch) => epoch,
                        Err(err) => {
                            error!("Cannot parse epoch from block {:?} - skip..", err);
                            continue;
                        },
                    };

                    let wallclock = match block.wallclock(&genesis_values).try_into() {
                        Ok(time) => time,
                        Err(err) => {
                            error!("Cannot parse wall time from block {:?} - skip..", err);
                            continue;
                        },
                    };

                    let slot = match block.slot().try_into() {
                        Ok(slot) => slot,
                        Err(err) => {
                            error!("Cannot parse slot from block {:?} - skip..", err);
                            continue;
                        },
                    };

                    match db
                        .index_follower_data(
                            slot,
                            network,
                            epoch,
                            wallclock,
                            hex::encode(block.hash().clone()),
                        )
                        .await
                    {
                        Ok(_) => (),
                        Err(err) => {
                            error!("unable to index follower data {:?} - skip..", err);
                            continue;
                        },
                    }

                    // Index the following:

                    // Utxo stuff

                    // Registration stuff

                    // Rewards stuff

                    // Last updated

                    // Refresh update metadata for future followers
                    match db
                        .refresh_last_updated(
                            chrono::offset::Utc::now(),
                            slot,
                            hex::encode(block.hash().clone()),
                            network.clone(),
                            machine_id.clone(),
                        )
                        .await
                    {
                        Ok(_) => (),
                        Err(err) => {
                            error!("unable to mark last update point {:?} - skip..", err);
                            continue;
                        },
                    };
                },
                ChainUpdate::Rollback(data) => {
                    let block = match data.decode() {
                        Ok(block) => block,
                        Err(err) => {
                            error!("unable to decode block {:?} - skip..", err);
                            continue;
                        },
                    };

                    info!(
                        "Rollback block NUMBER={} SLOT={} HASH={}",
                        block.number(),
                        block.slot(),
                        hex::encode(block.hash()),
                    );
                },
            }
        }
    });

    Ok(task)
}

/// In the context of setting up the follower config
/// If there is metadata present which allows us to bootstrap from a point in time
/// We start from there, if not; we start from genesis.
async fn generate_follower_config(
    start_from: (Option<SlotNumber>, Option<BlockHash>),
) -> Result<FollowerConfig, Box<dyn Error>> {
    let follower_cfg = if start_from.0.is_none() || start_from.1.is_none() {
        FollowerConfigBuilder::default().build()
    } else {
        FollowerConfigBuilder::default()
            .follow_from(Point::new(
                start_from
                    .0
                    .ok_or("Slot number not present - should be infallible")?
                    .try_into()?,
                hex::decode(
                    start_from
                        .1
                        .ok_or("Block Hash not present - should be infallible")?,
                )?,
            ))
            .build()
    };

    Ok(follower_cfg)
}
