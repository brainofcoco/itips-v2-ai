Native Face Recognition & Allowlisting
Create Face Group (Worker Database): Page 495 (Section 9.2.1)
.
Add Person (Upload Worker Face): Page 502 (Section 9.2.7)
.
[Event] FaceDetection: Page 518 (Section 9.2.15)
.
[Event] FaceRecognition: Page 520 (Section 9.2.16)
.
[Config] Face Recognition Event Handler Setting: Page 523 (Section 9.2.18)
.


9.2.1 Create Face Group
Method GET
groupName Request URL string R The face group name, max string
length is 127.
http://<server>/cgi-bin/faceRecognitionServer.cgi?action=createGroup
Request Params ( key=value format in URL )
Test1
Name Type R/O Description Example
Video Analyse APIs 495
groupDetail string O The description detail of the face
group, max string length is 255.
ForTest1
Request Example
http://192.168.1.108/cgi-
bin/faceRecognitionServer.cgi?action=createGroup&groupName=Test1&groupDetail=ForTest1
Response Params ( key=value format in body)
10000
Name Type R/O Description Example
R The identity of the created face
group, max string length is 63.
10000
Test1
groupID string Response Example
groupID=10000
9.2.2 Modify Face Group
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=modifyGroup
Request Params ( key=value format in URL )
Name Type R/O Description Example
R The identity of the face group, max
string length is 63.
R The name of the the face group, max
90
0
bin/faceRecognitionServer.cgi?action=modifyGroup&groupID=10000&groupName=Test1&groupDetail=
DAHUA_HTTP_API_V3.98 for Dunsin
Method GET
groupID string groupName string string length is 127.
groupDetail string O Description detail of the face group,
max string length is 255.
O Similarity threshold range [0,100] O Alive threshold [0,100] 0
O Mask similarity threshold range
[0,100]
http://192.168.1.108/cgi-
ForTest1
ForTest1
Similarity uint8 Alive uint8 MaskSimilarity uint8 Request Example
Response Params ( OK in body )
Response Example
OK
9.2.3 Delete Face Group
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=deleteGroup
Method GET
Request Params ( key=value format in URL )
Name Type R/O Description Example
Video Analyse APIs 496
groupID string R The identity of the face group, max
string length is 63.
10000
Request Example
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=deleteGroup&groupID=10000
Response Params ( OK in body )
Response Example
OK
10000
9.2.4 Deploy Face Group
There are two ways to deploy the group. One is based on the group (putDisposition), and the
another one is based on the channel (setGroup).
Deploy the face group to some video channels. If the video channel has been deployed already, it will
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=putDisposition
Request Params ( key=value format in URL )
Name Type R/O Description Example
R The identity of the face group, max
Video channel index which starts from
bin/faceRecognitionServer.cgi?action=putDisposition&groupID=10000&list[0].channel=1&list[0].similary
DAHUA_HTTP_API_V3.98 for Dunsin
 Put disposition to group
change the similary.
Method GET
groupID string string length is 63.
list object[] R List of disposition info.
int R
1.
int R The threshold of the face similary, 0
— 100.
http://192.168.1.108/cgi-
=80&list[1].channel=2&list[1].similary=70
Response Params ( key=value format in body)
[true, false]
1
+channel +similary Request Example
Name Type R/O Description Example
report bool[] R Result of putting disposition for each
request channel.
Response Example
report[0]=true
report[1]=false
 Delete some disposition from group
Remove the deployment of face group from some video channels.
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=deleteDisposition
Method GET
Video Analyse APIs 497
Request Params ( key=value format in URL )
Name Type R/O Description Example
groupID string R The identity of the face group, max
string length is 63.
10000
Video channel index which starts from
[1,2]
channel int[] R
1.
Request Example
http://192.168.1.108/cgi-
bin/faceRecognitionServer.cgi?action=deleteDisposition&groupID=10000&channel[0]=1&channel[1]=2
[true, false]
Response Params ( key=value format in body)
Name Type R/O Description Example
bool[] R Result of deleting disposition for each
request channel.
Response Example
Deploy some face groups to one video channel. If the video channel has been deployed already, it will
Note: This method will do an overwrite operation.
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=setGroup
Name Type R/O Description Example
[1,2]
10002
DAHUA_HTTP_API_V3.98 for Dunsin
report report[0]=true
report[1]=false
 set disposition group to channel
change the similary.
Request Params ( key=value format in URL )
Video channel index which starts from
O List of disposition info, if not exist,
remove all disposition from channel.
R The identity of the face group, max
string length is 63.
R The threshold of the face similary, 0
80
— 100.
Method GET
channel int[] R
1.
list object[] +groupID int +similary int Request Example
http://192.168.1.108/cgi-
bin/faceRecognitionServer.cgi?action=setGroup&channel=1&list[0].groupID=10000&list[0].similary=80&l
ist[1].groupID=10002&list[1].similary=75
Response Params ( OK in body )
Response Example
OK
 get disposition group from channel
Get the Deployment about the video channel.
Video Analyse APIs 498
Note: If the video channel does not deploy any group, then the response will be success with empty
http body.
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=getGroup
Method GET
Request Params ( key=value format in URL )
Name Type R/O Description Example
Video channel index which starts from
[1,2]
channel int[] R
1.
Request Example
[10001, 10002,
10003,…]
[80,75,82,…]
Response Example
9.2.5 Find Face Group
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=getGroup&channel=1
Response Params ( key=value format in body)
Name Type R/O Description Example
The identity of the face group, max
int[] R
string length is 63.
R The threshold of the face similary, 0
— 100.
Find the face group. If the groupID is not present in the URL, it will return all the groups.
Name Type R/O Description Example
DAHUA_HTTP_API_V3.98 for Dunsin
groupID similary int[] groupID[0]=10001
groupID[1]=10003
groupID[2]=10006
….
similary[0]=80
similary[1]=75
similary[2]=85
….
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=findGroup
Method GET
Request Params ( key=value format in URL )
10000
groupID char[] O The identity of the face group, max
string length is 63.
Request Example
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=findGroup
Response Params ( key=value format in body)
Name Type Description GroupList object[] R The face group information list.
+groupID char[] R/O R The identity of the face group, max string
length is 63.
Example
10000
Video Analyse APIs 499
+groupName char[] R Name of the face group, max string
length is 127.
test1
+groupDetail char[] O Description detail of the face group, max
string length is 255.
fortest1
+groupSize int R The number of face in this face group. 30
+channels int[] O Video channel index which starts from 0.
+similarity int[] O The threshold of the face similary.
+groupType char[] O The type of face group BlackListDB
+TimeSection char[][][] O The time section of face group
+FeatureState uint[4]
O The number of people in various states
in the group, and the number of people
who have not completed modeling
(feature extraction), cannot be identified
by the algorithm
[0] - The number of people preparing the
model, does not guarantee a certain
success of the model
[1] - The number of people who failed to
model, the picture does not meet the
requirements of the algorithm, and the
picture needs to be replaced
[2] - The number of people who have
been successfully modeled, and the data
can be used for face recognition by the
[3] - Remodeling is available for the
number of times that were once
DAHUA_HTTP_API_V3.98 for Dunsin
algorithm
successfully modeled, but became
unusable due to algorithm upgrades
Response Example
[100, 10, 200, 50]
GroupList[0].groupID=00001
GroupList[0].groupName=Test1
GroupList[0].groupDetail=ForTest1
GroupList[0].groupSize=30
GroupList[0].channels[0]=1
GroupList[0].channels[1]=2
…
GroupList[0].similarity[0]=80
GroupList[0].similarity[1]=75
…
GroupList[0].groupType=BlackListDB
GroupList[0].TimeSection[0][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[0][1]=0 00:00:00-23:59:59
GroupList[0].TimeSection[0][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[0][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[0][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[0][5]=0 00:00:00-23:59:59
Video Analyse APIs 500
GroupList[0].TimeSection[1][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[1][1]=0 00:00:00-23:59:59
GroupList[0].TimeSection[1][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[1][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[1][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[1][5]=0 00:00:00-23:59:59
GroupList[0].TimeSection[2][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[2][1]=0 00:00:00-23:59:59
GroupList[0].TimeSection[2][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[2][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[2][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[2][5]=0 00:00:00-23:59:59
GroupList[0].TimeSection[3][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[3][1]=0 00:00:00-23:59:59
GroupList[0].TimeSection[3][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[3][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[3][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[3][5]=0 00:00:00-23:59:59
GroupList[0].TimeSection[4][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[4][1]=0 00:00:00-23:59:59
GroupList[0].TimeSection[4][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[4][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[4][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[4][5]=0 00:00:00-23:59:59
GroupList[0].TimeSection[5][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[5][1]=0 00:00:00-23:59:59
DAHUA_HTTP_API_V3.98 for Dunsin
GroupList[0].TimeSection[5][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[5][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[5][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[5][5]=0 00:00:00-23:59:59
GroupList[0].TimeSection[6][0]=1 00:00:00-23:59:59
GroupList[0].TimeSection[6][1]=0 00:00:00-23:59:59
GroupList[0].TimeSection[6][2]=0 00:00:00-23:59:59
GroupList[0].TimeSection[6][3]=0 00:00:00-23:59:59
GroupList[0].TimeSection[6][4]=0 00:00:00-23:59:59
GroupList[0].TimeSection[6][5]=0 00:00:00-23:59:59
GroupList[1].groupID=00003
GroupList[1].groupName=Test3
GroupList[1].groupDetail=ForTest3
GroupList[1].groupSize=50
GroupList[1].channels[0]=1
GroupList[1].channels[1]=2
…
GroupList[1].similarity[0]=70
GroupList[1].similarity[1]=85
…
Video Analyse APIs 501
9.2.6 Re-Abstract Feature By Group
 Start ReAbstract
Abstract features for the groups.
About the process of the re-extract, the device will use an event named "FaceFeatureAbstract" to
report the process.
token int 12345
Response Example
token=12345
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=groupReAbstract
Method GET
Request Params ( key=value format in URL )
Name Type R/O Description Example
char[][64] R The identity of the face group, max
["10000","10001"]
string length is 63.
Request Example
http://192.168.1.108/cgi-
bin/faceRecognitionServer.cgi?action=groupReAbstract&groupID[0]=10000&groupID[1]=10001
Response Params ( key=value format in body)
Name Type R/O Description Example
R The identity of this operation. Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=stopGroupReAbstract
Name Type R/O Description Example
12345
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=stopGroupReAbstract&token=12345
DAHUA_HTTP_API_V3.98 for Dunsin
groupID Request Params ( key=value format in URL )
R The identity of this operation.  Stop ReAbstract
Method GET
token int Request Example
Response Params ( OK in body )
Response Example
OK


9.2.7 Add Person
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=addPerson
Method POST
Request Params ( key=value format in URL , binary data in body)
Name Type R/O Description Example
Video Analyse APIs 502
groupID char[] R The identity of the face group that this
person to add. Max string length is 63.
10000
name char[] R The person name, max string length is
63.
ZhangSan
birthday char[] O The person's birthday, ex: "1980-01-
01".
"1980-01-01"
sex char[] O Sex, it can be "Male", "Female",
"Unknown".
Male
country char[] O
The country name, length must be 2,
and value should be according to
ISO3166.
CN
province char[] O The province name, max string length
is 63.
XXX
city char[] O The city name, max string length is 63. YYY
certificateType char[] O The certificate type. It can be: "IC'
,
"Passport", "Unknown".
IC
id char[] O The ID of certificate type, max string
length is 31.
3333333333333
className academe char[] char[] O max string length is 63.
O max string length is 63.
POST http://<server>/cgi-bin/faceRecognitionServer.cgi?action=addPerson&groupID=10000&name=Zh
angSan&birthday=1980-01-05&sex=Male&country=CN&province=XXX&city=YYY HTTP/1.1
Name Type R/O Description Example
"0005"
DAHUA_HTTP_API_V3.98 for Dunsin
Request Example
Content-Type: image/jpeg
Content-Length: <image size>
<JPEG image data>
Response Params ( key=value format in body)
uid char[32] R The id for this Person, max string
length is 31.
Response Example
uid=0005
9.2.8 Modify Person
Modify a person's info.
Note: If you do not want to change the image about the person, the request should not contain the image
data.
Note: You should provide at lease one optional param to update.
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=modifyPerson
Method POST
Request Params ( key=value format in URL , binary data in body)
Name Type R/O Description Example
Video Analyse APIs 503
uid char[] R The identity of the Person, max string
length is 31.
0005
groupID char[] R The identity of the Face Group that this
Person in. max string length is 63.
10000
name
char[] O The person's name, max string length
is 63.
ZhangSan
birthday char[] O The person's birthday, ex: "1980-01-
01".
"1980-01-01"
sex
char[] O Sex, it can be "Male", "Female",
"Unknown".
Male
char[]
The country name, length must be 2,
CN
country
O
and value should be according to
ISO3166.
province char[] O The province name, max string length
is 63.
XXX
city char[] O The city name, max string length is 63. YYY
certificateType char[] O The certificate type. It can be: "IC'
,
"Passport", "Unknown".
IC
id char[] O The ID of certificate type, max string
length is 31.
3333333333333
className char[] O max string length is 63.
academe char[] O max string length is 63.
Request Example
POST http://<server>/cgi-bin/faceRecognitionServer.cgi?action=modifyPerson&uid=0005&groupID=100
00&name=ZhangSan&birthday=1980-01-05&sex=Male&country=CN&province=XXX&city=YYY HTTP/
DAHUA_HTTP_API_V3.98 for Dunsin
1.1
Content-Type: image/jpeg
Content-Length: <image size>
<JPEG image data>
Response Params ( OK in body )
Response Example
OK
9.2.9 Delete Person
Request Params ( key=value format in URL )
Name Type R/O Description Example
uid char[] R The identity of the person, max string
length is 31.
001
groupID char[] R The identity of the face group that this
Person in. max string length is 63.
10000
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=deletePerson
Method GET
Request Example
Video Analyse APIs 504
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=deletePerson&uid=001&groupID=10000
Response Params ( OK in body )
Response Example
OK
9.2.10 Find Person
["10000","10001"]
char[] +EndRegisterStor
ageTime
char[]  Start to find
Note: the returned token will be expired after 60 seconds without any doFind call.
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=startFind
Method GET
Request Params ( key=value format in URL )
Name Type R/O Description Example
R Search scope condition.
R The list of identity of the face
group, max string length is
63.
O Start time, Start time and end
time filter the check-in time of
the personnel registered
library
xxx
"Female"
CN
DAHUA_HTTP_API_V3.98 for Dunsin
condition object char[][] +GroupID
+StartRegisterSto
rageTime
O End time O Person condition.
O Person Name, max string
length is 63.
O Sex, it can be "Male",
"Female", "Unknown".
O Country name, length must
be 2, and value should be
according to ISO3166.
O Province name, max string
length is 63.
O City name, max string length
is 63.
O Certificate Type. It can be:
"IC'
, "Passport", "Unknown".
"2010-05-25 00:00:00"
"2010-05-25 23:59:59"
person object +Name char[] +Sex char[] char[] +Country
+Province char[] +City char[] +CertificateType char[] Passport
+ID char[] O Person ID of CertificateType,
max string length is 31.
int +FeatureState
O Feature State, 0:Unknown,
1:Failed, 2:OK.
1
className char[] O max string length is 63.
academe char[] O max string length is 63.
Request Example
Video Analyse APIs 505
http://<server>/cgi-
bin/faceRecognitionServer.cgi?action=startFind&condition.GroupID[0]=10000&condition.GroupID[1]=10
003&person.Sex=Male&person.Country=CN&person.FeatureState=1
Response Params ( key=value format in body)
Name Type R/O Description Example
Token for this search, use this
123456789
token uint R
token to get result and stop
search.
totalCount int R Result num, return -1 means
24
still searching.
Response Example
token=123456789
totalCount=24
Note: the returned token will be expired after 60 seconds without any doFind call.
http://<server>/cgi-bin/faceRecognitionServer.cgi?action=doFind
Request Params ( key=value format in URL )
Name Type R/O Description Example
Token for this search, use this
123456789
token to get result and stop
The index in search result,
0
should between 0 and
http://<server>/cgi-bin/faceRecognitionServer.cgi?action=doFind&token=123456789&index=0
Response Params ( multipart, key=value format in body, binary in body)
DAHUA_HTTP_API_V3.98 for Dunsin
 Get find result
Request URL Method GET
token uint R
search.
index uint R
totalCount –1.
Request Example
01".
Name Type R/O Description Example
person object R Person condition.
+UID string R The identity of the person, max string
length is 31.
0005
+GroupID string R The identity of the face group that this
Person in. max string length is 63.
10000
+Name string R The person name, max string length
is 63.
xxx
+Sex string O Sex, it can be "Male", "Female",
"Unknown".
Female
+PicUrl string O Personnel picture URL "/mnt/mtd/database/Fac
eRecognition/1/1.jpg"
+Birthday string O The person's birthday, ex: "1980-01-
"1980-01-01"
Video Analyse APIs 506
string +Country
O Country name, length must be 2, and
value should be according to
ISO3166.
CN
+Province string O Province name, max string length is
63.
+City string O City name, max string length is 63.
+CertificateTy
pe
string O Certificate Type, can be: "IC'
,
"Passport", "Unknown".
Passport
+ID 1234567890
0
--<boundary>
Content-Type: text/plain
Content-Length: <length>
person.UID=0005
person.GroupID=10000
person.Name=ZhangSan
person.Birthday=1980-01-01
person.Sex=Male
person.Country=CN
person.Province=XXX
person.City=YYY
person.CertificateType=IC
person.ID=1234567890
person.FeatureState=0
--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>
string O Person ID of CertificateType, max
string length is 31.
int O Feature State, 0:Unknown, 1:Failed,
+FeatureState
2:OK.
className char[] O max string length is 63.
char[] O max string length is 63.
Response Example
HTTP/1.1 200 OK
Content-Type: multipart/x-mixed-replace; boundary=<boundary>
DAHUA_HTTP_API_V3.98 for Dunsin
academe Server: Device/1.0
Content-Length: <length>
< jpeg image data ... >
--<boundary>--
 Stop finding
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=stopFind
Method GET
Request Params ( key=value format in URL )
Video Analyse APIs 507
Name Type R/O Description Example
token uint R The token for this search, use this token
to get result and stop search.
123456789
Request Example
http://<server>/cgi-bin/faceRecognitionServer.cgi?action=stopFind&token=123456789
Response Params ( OK in body )
Response Example
OK
["10000"
, "10001"]
Request Example
9.2.11 Re-Abstract Features By Person
About the process of the re-extract, the device will use an event named "FaceFeatureAbstract"to
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=reAbstract
Request Params ( key=value format in URL )
Name Type R/O Description Example
O The list of identity of person, max
string length is 31.
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=reAbstract&uid[0]=001&uid[1]=002
Request URL http://<server>/cgi-bin/faceRecognitionServer.cgi?action=stopReAbstract
DAHUA_HTTP_API_V3.98 for Dunsin
 Start ReAbstract
report the process.
Method GET
UID char[][31] Response Params ( OK in body )
Response Example
OK
 Stop ReAbstract
Method GET
Request Params ( none )
Name Type R/O Description Example
Request Example
http://192.168.1.108/cgi-bin/faceRecognitionServer.cgi?action=stopReAbstract
Response Params ( OK in body )
Response Example
OK


# Dahua Face Recognition API Documentation

---

# 9.2.16 [Event] FaceRecognition

When the video channel disposition with some face group, and the video channel detects a face, after recognition in the face groups, send this event.

## Event Information

| Field | Value |
|---|---|
| Event Code | FaceRecognition |
| Event Action | Pulse |
| Event Index | 0 |

---

# Event Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| UID | string | R | Identity of the person, max length 31 | |
| Candidates | object[] | O | Candidate person list | |
| Person | object | O | Candidate person information | |
| Person.UID | string | R | Identity of the person | |
| Person.GroupID | string | R | Face group identity | |
| Person.Name | string | O | Person name, max length 63 | ZhangSan |
| Person.Birthday | string | O | Birthday format `1980-01-01` | 2000-01-01 |
| Person.Sex | string | O | Male / Female / Unknown | Man |
| Person.Country | string | O | ISO3166 country code | |
| Person.Province | string | O | Province name | |
| Person.City | string | O | City name | |
| Person.CertificateType | string | O | IC / Passport / Unknown | |
| Person.ID | string | O | Person ID | |
| Similarity | int | O | Similarity score (1–100) | 89 |
| Face | object | O | Face attribute information | |
| Face.Sex | string | O | Man / Woman | Man |
| Face.Age | int | O | Age | 23 |
| Face.Feature | string[] | O | Face features | ["WearGlasses", "Anger"] |

---

# Supported Face Features

```txt
WearGlasses
SunGlasses
NoGlasses
Smile
Anger
Sadness
Disgust
Fear
Surprise
Neutral
Laugh
Happy
Confused
Scream
```

---

# Additional Face Attributes

| Name | Type | Description | Example |
|---|---|---|---|
| Face.Eye | int | 0 = not detected, 1 = closed, 2 = open | 1 |
| Face.Mouth | int | 0 = not detected, 1 = closed, 2 = open | 1 |
| Face.Mask | int | 0 = not detected, 1 = no mask, 2 = wearing mask | 1 |
| Face.Beard | int | 0 = not detected, 1 = no beard, 2 = beard | 1 |

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=FaceRecognition
Events[0].EventBaseInfo.Action=Pulse
Events[0].EventBaseInfo.Index=0

Events[0].UID=00105

Events[0].Candidates[0].Person.UID=0012
Events[0].Candidates[0].Person.GroupID=10000
Events[0].Candidates[0].Person.Name=ZhangSan
Events[0].Candidates[0].Person.Birthday=1980-01-02
Events[0].Candidates[0].Person.Sex=Male
Events[0].Candidates[0].Similarity=80

Events[0].Candidates[1].Person.UID=0014
Events[0].Candidates[1].Person.GroupID=10000
Events[0].Candidates[1].Person.Name=Lisi
Events[0].Candidates[1].Person.Birthday=1980-01-05
Events[0].Candidates[1].Person.Sex=Male
Events[0].Candidates[1].Similarity=75

Events[0].Face.Sex=Man
Events[0].Face.Age=20
Events[0].Face.Feature[0]=SunGlasses
Events[0].Face.Feature[1]=Smile
Events[0].Face.Eye=2
Events[0].Face.Mouth=1
Events[0].Face.Mask=1
Events[0].Face.Beard=2
```

---

# 9.2.17 [Event] FaceFeatureAbstract

When Re-Abstract Feature By Group or By Person, the abstract progress detail will send in this event.

## Event Information

| Field | Value |
|---|---|
| Event Code | FaceFeatureAbstract |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Infos | object[100] | R | Abstract detail information | |
| State | string | R | Success / False / Progress | Progress |
| Process | int | O | Abstract progress percentage | 30 |
| UID | string | O | Person identity | 20005 |
| GroupID | string | O | Face group identity | 10000 |

---

# Event Response Example

```json
{
  "Code": "FaceFeatureAbstract",
  "Action": "Start",
  "Index": 0,
  "Data": {
    "Infos": [
      {
        "State": "Progress",
        "Process": 30,
        "UID": "20005",
        "GroupID": "10000"
      }
    ]
  }
}
```

---

# 9.2.18 [Config] Face Recognition Event Handler Setting

## Config Data Params

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| FaceRecognitionEventHandler | object[] | R | Face group event config array | |
| GroupID | char[] | R | Face group ID | 0017 |
| GroupName | char[] | R | Face group name | wsd |
| EventEnableMask | int | R | Report event mask | 3 |
| RecordEnableMask | int | R | Record media file mask | 0 |
| RecordLatch | int | R | Record latch time (seconds) | 10 |
| SnapEnableMask | int | R | Snapshot enable mask | 3 |
| MailEnableMask | int | R | Send mail enable mask | 0 |

---

# Bit Masks

## EventEnableMask

| Bit | Meaning |
|---|---|
| Bit 0 | Recognition success |
| Bit 1 | Recognition failed |

---

## SnapEnableMask

| Bit | Meaning |
|---|---|
| Bit 0 | Recognition success |
| Bit 1 | Recognition failed |

---

# Get Config Example

```http
GET /cgi-bin/configManager.cgi?action=getConfig&name=FaceRecognitionEventHandler
```

---

# Get Config Response Example

```txt
table.FaceRecognitionEventHandler[0].GroupID=0017
table.FaceRecognitionEventHandler[0].GroupName=wsd
table.FaceRecognitionEventHandler[0].EventEnableMask=3
table.FaceRecognitionEventHandler[0].RecordEnableMask=0
table.FaceRecognitionEventHandler[0].RecordLatch=10
table.FaceRecognitionEventHandler[0].SnapEnableMask=3
table.FaceRecognitionEventHandler[0].MailEnableMask=0
```

---

# Set Config Example

```http
GET /cgi-bin/configManager.cgi?action=setConfig
```

## Parameters

```txt
FaceRecognitionEventHandler[0].GroupID=0017
FaceRecognitionEventHandler[0].GroupName=wsd
FaceRecognitionEventHandler[0].EventEnableMask=3
FaceRecognitionEventHandler[0].RecordEnableMask=0
FaceRecognitionEventHandler[0].RecordLatch=10
FaceRecognitionEventHandler[0].SnapEnableMask=3
FaceRecognitionEventHandler[0].MailEnableMask=0
```

---

# Set Config Response

```txt
OK
```

---

# 9.2.19 [Config] Face-ID Recognition Threshold

## Request URL

```http
http://<server>/cgi-bin/configManager.cgi?action=setConfig
```

## Method

```txt
GET
```

---

# Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| CitizenPictureCompareRule | object | R | Citizen picture compare rule | |
| Threshold | uint8 | R | Face-ID comparison threshold [1–100] | 60 |

---

# Request Example

```http
http://192.168.1.108/cgi-bin/configManager.cgi?action=setConfig&CitizenPictureCompareRule.Threshold=60
```

---

# Response

```txt
OK
```

---

# 9.2.20 Export Face Database

The exported data is binary.

---

# Request URL

```http
http://<server>/cgi-bin/api/FaceLibInfoExport/export
```

---

# Method

```txt
POST
```

---

# Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| GroupID | char[64] | R | Person group ID | "10" |
| Password | char[64] | O | Unzip password | "abcd" |
| TaskType | enumchar[64] | O | Task type | "FaceGroup" |

---

# Example Request Body

```json
{
  "GroupID": "10",
  "Password": "abcd",
  "TaskType": "FaceGroup"
}
```

---

# Example Response

```txt
Binary Export Data
```