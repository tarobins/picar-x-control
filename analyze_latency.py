#!/usr/bin/env python3
import os
import json
import sys

def analyze():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(script_dir, 'latency_trace.log')
    
    if not os.path.exists(log_path):
        print(f"No latency log found at {log_path} yet. Please interact with the UI first (drive/gimbal) to generate data.")
        return

    records = []
    with open(log_path, 'r') as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except Exception as e:
                print(f"Warning: Failed to parse line {line_num}: {e}")

    if not records:
        print("Latency log is empty. Try driving the car in the dashboard first.")
        return

    print(f"=== LATENCY REPORT ({len(records)} samples) ===")
    print(f"{'Type':<8} | {'Total RT':<10} | {'Client->Proxy':<13} | {'Proxy->Robot RT':<15} | {'Robot Exec':<11} | {'SSH Transit':<11}")
    print("-" * 80)

    total_rts = []
    client_proxies = []
    proxy_robot_rts = []
    robot_execs = []
    ssh_transits = []

    delayed_count = 0
    timeout_count = 0
    error_count = 0

    for r in records:
        status = r.get("status", "success")
        t_client_sent = r["t_client_sent"]
        t_client_recv = r["t_client_received"]

        # Check if the request was timed out or failed
        if status == "timeout":
            timeout_count += 1
            print(f"{r['type']:<8} | {'N/A':>9} | {'N/A':>13} | {'N/A':>15} | {'N/A':>11} | {'N/A':>11} ❌ TIMEOUT (1.5s)")
            continue
        elif status == "error" or r.get("t_proxy_received") is None or r.get("t_robot_received") is None:
            error_count += 1
            err_msg = r.get("error") or "Unknown error"
            print(f"{r['type']:<8} | {'N/A':>9} | {'N/A':>13} | {'N/A':>15} | {'N/A':>11} | {'N/A':>11} ❌ ERROR ({err_msg})")
            continue
            
        t_proxy_recv = r["t_proxy_received"]
        t_proxy_sent = r["t_proxy_sent"]
        t_proxy_back = r["t_proxy_back"]
        t_robot_recv = r["t_robot_received"]
        t_robot_done = r["t_robot_done"]

        # 1. Total Roundtrip (UI -> Client -> Proxy -> Robot -> back to UI)
        total_rt = t_client_recv - t_client_sent
        
        # 2. Client-Proxy Roundtrip segment (Total Client RT minus time spent forwarding)
        proxy_rt = t_proxy_back - t_proxy_sent
        client_proxy = total_rt - proxy_rt
        
        # 3. Time spent executing command on the physical hardware
        robot_exec = t_robot_done - t_robot_recv
        
        # 4. Net SSH network transit roundtrip (Total proxy RT minus actual robot execution time)
        ssh_transit = proxy_rt - robot_exec

        total_rts.append(total_rt)
        client_proxies.append(client_proxy)
        proxy_robot_rts.append(proxy_rt)
        robot_execs.append(robot_exec)
        ssh_transits.append(ssh_transit)

        # Highlight delayed commands (e.g. > 150ms roundtrip)
        tag = ""
        if total_rt > 150.0:
            tag = "⚠️ DELAYED"
            delayed_count += 1

        print(f"{r['type']:<8} | {total_rt:>7.1f}ms | {client_proxy:>10.1f}ms | {proxy_rt:>12.1f}ms | {robot_exec:>8.1f}ms | {ssh_transit:>8.1f}ms {tag}")

    if not total_rts and (timeout_count > 0 or error_count > 0):
        print("\n=== SUMMARY STATISTICS ===")
        print("No successful commands to show latency statistics.")
        print(f"Timeouts: {timeout_count}")
        print(f"Errors: {error_count}")
        return
    elif not total_rts:
        print("No valid trace records found.")
        return

    print("\n=== SUMMARY STATISTICS ===")
    def stats(arr, name):
        avg = sum(arr) / len(arr)
        mx = max(arr)
        mn = min(arr)
        print(f"{name:<25} | Avg: {avg:>6.1f}ms | Max: {mx:>6.1f}ms | Min: {mn:>6.1f}ms")

    stats(total_rts, "Total Roundtrip")
    stats(client_proxies, "Client-Proxy Segment")
    stats(proxy_robot_rts, "Proxy-Robot Roundtrip")
    stats(robot_execs, "Robot Hardware Execution")
    stats(ssh_transits, "SSH Tunnel Transit RT")
    
    total_samples = len(total_rts) + timeout_count + error_count
    print(f"\nDelayed commands (>150ms): {delayed_count} / {len(total_rts)} ({delayed_count/len(total_rts)*100:.1f}%)")
    print(f"Timed out / Lost commands : {timeout_count} / {total_samples} ({timeout_count/total_samples*100:.1f}%)")
    print(f"Failed / Error commands   : {error_count} / {total_samples} ({error_count/total_samples*100:.1f}%)")

if __name__ == '__main__':
    analyze()
