#include <bits/stdc++.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
using namespace std;

struct Cfg{ string ip="127.0.0.1"; int port=5000; int k=5; int p0=0; int reps=1; } cfg;

static string slurp(const string& path){ ifstream f(path); return string((istreambuf_iterator<char>(f)), {}); }
static string getStr(const string& j, const string& key){ auto p=j.find("\""+key+"\""); if(p==string::npos) return ""; p=j.find(':',p); p=j.find('"',p); auto q=j.find('"',p+1); return j.substr(p+1, q-p-1); }
static int getInt(const string& j, const string& key){ auto p=j.find("\""+key+"\""); if(p==string::npos) return 0; p=j.find(':',p)+1; while(p<j.size() && isspace(j[p])) p++; int v=0,sgn=1; if(j[p]=='-'){sgn=-1;p++;} while(p<j.size()&&isdigit(j[p])) v=v*10+(j[p++]-'0'); return sgn*v; }

static bool request_once(const string& ip, int port, int p, int k, string& line){
    int fd=socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in srv{}; srv.sin_family=AF_INET; srv.sin_port=htons(port);
    inet_pton(AF_INET, ip.c_str(), &srv.sin_addr);
    if(connect(fd,(sockaddr*)&srv,sizeof(srv))<0){ perror("connect"); return false; }
    string req = to_string(p)+","+to_string(k)+"\n";
    if(send(fd, req.data(), req.size(), 0)<0){ perror("send"); close(fd); return false; }
    line.clear(); char buf[512];
    while(true){
        ssize_t n=recv(fd, buf, sizeof(buf), 0);
        if(n<=0){ close(fd); return false; }
        line.append(buf, buf+n);
        auto pos=line.find('\n'); if(pos!=string::npos){ line.resize(pos); break; }
    }
    close(fd); return true;
}

int main(){
    ios::sync_with_stdio(false);
    string j=slurp("config.json");
    if(j.size()){
        string ip=getStr(j,"server_ip"); if(!ip.empty()) cfg.ip=ip;
        int port=getInt(j,"server_port"); if(port>0) cfg.port=port;
        int k = getInt(j,"k");
        cfg.k = k;
        cfg.p0=getInt(j,"p");
        int r=getInt(j,"num_iterations"); if(r<=0) r=getInt(j,"num_repetitions");
        if(r>0) cfg.reps=r;
    }

    if (cfg.k <= 0) {
        // Nothing to fetch; exit quietly
        return 0;
    }

    unordered_map<string,int> freq;
    int p=cfg.p0;
    while(true){
        string line;
        if(!request_once(cfg.ip, cfg.port, p, cfg.k, line)){ cerr<<"request failed\n"; return 1; }
        istringstream ss(line); string tok; bool eof=false;
        while(getline(ss, tok, ',')){
            if(tok=="EOF"){ eof=true; break; }
            if(!tok.empty()) freq[tok]++;
        }
        if(eof) break;
        p += cfg.k;
    }
    vector<pair<string,int>> v(freq.begin(), freq.end());
    sort(v.begin(), v.end());
    for(auto& [w,c]: v) cout<<w<<", "<<c<<"\n";
    return 0;
}
